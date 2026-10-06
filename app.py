from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash, jsonify, abort
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from flask import g
import sys
import threading
import os
import logging
import re
import time
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Tiene que ejecutarse ANTES de importar db/auth: ambos leen variables de
# entorno (ADMIN_EMAILS, GOOGLE_CLIENT_ID, etc.) al nivel de módulo, en el
# momento del import, así que si el .env se carga después esas variables
# quedan vacías aunque estén bien puestas en el archivo.
load_dotenv()

from form_processor import FormProcessor
from algorythms import Document_process
from IA import generate_essay_content, generate_introduction, generate_conclusion, GenerationError
from title_check import check_title, check_glossary
from text_format import format_texts
from glossary import extract_terms, parse_terms, generate_glossary, generate_bibliography
from report_scan import extract_assignment

import db
import jobs
import landing
import ai_provider
import rate_limit
from auth import auth_bp, current_user, login_required
from admin import admin_bp
import plans
import retention
import storage
from plans import plans_bp

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
# Lax: el navegador no manda la cookie de sesión en POST desde otros sitios,
# que es lo que protege las acciones del panel admin de peticiones falsas.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024
# Las generaciones corren en segundo plano (jobs.py); bajo pytest, en el acto y dentro de la petición.
app.config['GENERATION_INLINE'] = 'pytest' in sys.modules
app.secret_key = os.getenv('SECRET_KEY', '7f8b9a2c3d4e5f608192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8')


# Detrás del proxy de Azure/DigitalOcean la app ve http y la IP del proxy: con
# TRUST_PROXY=1 se respetan X-Forwarded-* (esquema https en los enlaces
# firmados, el redirect de Google y la IP real). SESSION_COOKIE_SECURE=1 manda
# la cookie de sesión solo por https.
if os.getenv('TRUST_PROXY') == '1':
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if os.getenv('SESSION_COOKIE_SECURE') == '1':
    app.config['SESSION_COOKIE_SECURE'] = True

# Enlaces firmados: el vencimiento se comprueba contra el documento guardado.
SHARE_FILETYPES = ('docx', 'pdf')


def document_filetypes(doc):
    user = current_user()
    if not db.public_plans_enabled() or (user and user['is_admin']):
        return SHARE_FILETYPES
    return ('docx',) if doc['billing_plan'] == 'recharge' else SHARE_FILETYPES


def _share_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='share-file')


db.init_app(app)
rate_limit.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(plans_bp)


@app.before_request
def load_ai_settings():
    g.ai_settings_token = ai_provider.request_settings.set(db.get_settings())
    g.ai_trace_token = ai_provider.generation_trace.set([])


@app.teardown_request
def clear_ai_settings(_error):
    trace_token = g.pop('ai_trace_token', None)
    if trace_token is not None:
        ai_provider.generation_trace.reset(trace_token)
    token = g.pop('ai_settings_token', None)
    if token is not None:
        ai_provider.request_settings.reset(token)


@app.context_processor
def inject_current_user():
    user = current_user()
    settings = db.get_settings()
    free_ai_limit = plans.parse_limit(settings['free_ai_limit'])
    can_start_free = not db.public_plans_enabled(settings) or (
        settings['free_ai_enabled'] == '1' and (free_ai_limit is None or free_ai_limit > 0))
    hours = db.billing_state(user)['hours'] if user and not user['is_admin'] else db.get_retention_hours()
    from glossary import max_terms
    return {'current_user': current_user(), 'retention_hours': hours,
            'can_start_free': can_start_free,
            'plans_visible': db.public_plans_enabled() or bool(user and user['is_admin']),
            'retention_text': db.format_duration(hours), 'glossary_limit': max_terms(),
            'billing': db.billing_state(user) if user else None,
            'glossary_allowed': plans.glossary_access(user),
            'glossary_bibliography_allowed': plans.glossary_bibliography_access(user),
            'public_glossary_limit': max((p['terms'] for p in db.plan_catalog().values() if p['enabled'] or not db.public_plans_enabled()), default=100)}


@app.route('/validate_title', methods=['POST'])
@login_required
@rate_limit.rate_limit(10, 60, key_func=rate_limit.per_user)
def validate_document_title():
    allowed, reason = plans.generation_access(current_user(), 'ai')
    if not allowed:
        return jsonify(error=reason), 403
    title = (request.form.get('title') or '').strip()
    error = validate_title_field(title)
    if error:
        return jsonify(error=error), 400
    session.pop('validated_title', None)
    usage = []
    try:
        verdict = check_title(title, usage_sink=usage)
    except GenerationError as exc:
        return jsonify(error=f'No se pudo validar el título. {exc.user_message}'), 503
    if not verdict.valid:
        return jsonify(error=verdict.message(title)), 400
    session['validated_title'] = {'title': title.casefold(), 'at': time.time(), 'tokens': sum(usage)}
    return jsonify(valid=True)


# ── Validaciones de la generación ─────────────────────────────────────

TITLE_MIN_LEN = 5      # igual que la validación del formulario (builder.js)
TITLE_MAX_LEN = 300    # maxlength del campo título
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|.#%~{}&\x00-\x1f]+')


# Nombres que Windows no permite como archivo (con cualquier extensión).
_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL',
                   *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def safe_filename(name):
    """Nombre de archivo seguro a partir del título: sin barras, dos puntos,
    comillas ni otros caracteres que rompían la ruta de salida o creaban
    subcarpetas; sin puntos ni guiones bajos en los extremos; sin nombres
    reservados de Windows (CON, NUL, COM1…); y de largo acotado. Es la última
    capa de validación antes de crear el archivo."""
    cleaned = _FILENAME_BAD.sub('_', name).strip(' ._')[:80].strip(' ._')
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned += '_doc'
    return cleaned or 'documento'


def validate_title_field(title):
    """Mensaje de error si el título no sirve, o None si está bien. Se valida
    en el servidor porque el JavaScript del formulario se puede saltar."""
    title = (title or '').strip()
    if len(title) < TITLE_MIN_LEN:
        return f'Escribe el título del trabajo (mínimo {TITLE_MIN_LEN} caracteres).'
    if len(title) > TITLE_MAX_LEN:
        return f'El título es demasiado largo (máximo {TITLE_MAX_LEN} caracteres).'
    if not re.search(r'[^\W\d_]', title):
        return 'El título debe tener palabras, no solo números o símbolos.'
    return None


def form_error(message, endpoint='show_form'):
    """Vuelve al formulario mostrando POR QUÉ no se generó el documento."""
    flash(message, 'error')
    return redirect(url_for(endpoint))


app.jinja_env.globals['logo_thumb'] = landing.thumb_path


@app.route('/')
def welcome():
    universities, title = landing.preview(db.get_settings())
    return render_template('main_page.html', preview_universities=universities, preview_title=title)


@app.route('/sw.js')
def service_worker():
    response = send_file('static/sw.js', mimetype='application/javascript', max_age=0)
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/privacy')
def privacy():
    return render_template('legal_page.html', privacy_page=True)


@app.route('/terms')
def terms():
    return render_template('legal_page.html', privacy_page=False)

@app.route('/bach')
@login_required
def show_form_bach():
    session.pop('file_generated', None)
    allowed, reason = plans.generation_access(current_user(), 'manual')
    if not allowed:
        return plans.redirect_to_plans(reason)
    return render_template('bachiller.html')

@app.route('/process_form_bach', methods=['POST'])
@login_required
@rate_limit.rate_limit(10, 60, key_func=rate_limit.per_user)
@plans.with_generation_quota
def process_form_bach():
    user = current_user()
    # Bachillerato siempre se escribe a mano: cuenta como documento manual.
    allowed, reason = plans.generation_access(user, 'manual')
    if not allowed:
        return plans.redirect_to_plans(reason)

        # Retrieve form data
    form_data = request.form
    title_error = validate_title_field(form_data.get('title'))
    if title_error:
        return form_error(title_error, 'show_form_bach')

    processor = FormProcessor(form_data, 'bach')
    processor.process()
    replacements, head_title = processor.generate_replacements()
    introduccion = processor.introduccion
    body = processor.body
    #body = generate_essay_content(processor.title)
    #if body != '':
        #introduccion = generate_introduction(processor.title, body)
    conclusion = processor.conclusion
    bibliography = form_data.get('bibliografia', '').strip() if 'incluir_bibliografia' in form_data else ''

    input_doc='input/plantilla_bach.docx'
    input_doc2='input/plantilla_bachempty.docx'

  # Check if the file exists
    file_stem = safe_filename(head_title)
    # Nombre libre en el almacenamiento (dos usuarios pueden tener el mismo título).
    full_stem = storage.unique_stem(file_stem)
    docx_output = f'output/{full_stem}.docx'

    university_name = form_data.get('u', '')
    try:
        Document_process.fill_placeholders(docx_output, input_doc, input_doc2, replacements,
                                            introduccion, body, conclusion, head_title, 'bach',
                                            university_name=university_name, bibliography=bibliography)
    except Exception:
        logging.exception('Error armando el documento "%s"', head_title)
        return form_error('Ocurrió un error armando el documento. Inténtalo de nuevo; '
                          'si se repite, avisa al administrador.', 'show_form_bach')

    if not os.path.isfile(docx_output):
        return form_error('El documento no se pudo guardar. Inténtalo de nuevo.', 'show_form_bach')
    if not (body.strip() or introduccion.strip() or conclusion.strip() or bibliography.strip()):
        flash('El documento tiene solo la portada porque dejaste el contenido en blanco.', 'warning')
    if not os.path.isfile(docx_output[:-5] + '.pdf'):
        flash('No se pudo generar el PDF; solo está disponible la versión Word.', 'warning')
    try:
        storage.publish(full_stem)
    except storage.StorageError:
        return form_error('No se pudo guardar el documento. Inténtalo de nuevo.', 'show_form_bach')

    db.record_document(user['id'], head_title, 'bach', tokens_used=0, mode='manual',
                       file_stem=full_stem)
    session['file_generated'] = True
    session['document_kind'] = 'report'

    # Redirect to a new page or indicate success
    return redirect(url_for('choose_file', filename=full_stem))
    #return redirect(url_for('index'))

@app.route('/form')
@login_required
def show_form():
    session.pop('file_generated', None)
    access = plans.access_summary(current_user())
    if access['ai']['reason'] == 'free_mode_limit':
        flash('Alcanzaste tus 5 generaciones gratuitas. Próximamente estarán disponibles los planes.', 'warning')
    if not access['ai']['ok'] and not access['manual']['ok'] and access['ai']['reason'] != 'free_mode_limit':
        # Ningún modo disponible: no tiene sentido mostrar el formulario.
        return plans.redirect_to_plans(access['ai']['reason'])
    return render_template('universitario.html', access=access)


@app.route('/glossary/terms', methods=['POST'])
@login_required
@rate_limit.rate_limit(6, 60, key_func=rate_limit.per_user)
def glossary_terms():
    if not plans.glossary_access(current_user()):
        return jsonify(error='La recarga no incluye glosarios. Elige un plan mensual.'), 403
    allowed, _ = plans.generation_access(current_user(), 'ai')
    if not allowed:
        return jsonify(error='Necesitas acceso a EduLab AI para leer la lista. Consulta los planes.'), 403
    upload = request.files.get('terms_file')
    if upload is None or not upload.filename:
        return jsonify(error='Selecciona una imagen o documento con los términos.'), 400
    usage = []
    try:
        terms = extract_terms(upload, usage_sink=usage)
        return jsonify(terms=terms, count=len(terms))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except GenerationError as exc:
        return jsonify(error=exc.user_message), 502
    finally:
        session['glossary_extraction_tokens'] = session.get('glossary_extraction_tokens', 0) + sum(usage)


SCAN_MIN_INTERVAL = 3      # segundos entre lecturas de foto: cada una gasta tokens de IA


@app.route('/report/scan', methods=['POST'])
@login_required
@rate_limit.rate_limit(6, 60, key_func=rate_limit.per_user)
def report_scan():
    """Lee la foto de una consigna y devuelve el título y los temas para el formulario."""
    allowed, _ = plans.generation_access(current_user(), 'ai')
    if not allowed:
        return jsonify(error='Necesitas acceso a EduLab AI para leer la foto. Consulta los planes.'), 403
    now = time.time()
    if now - session.get('scan_at', 0) < SCAN_MIN_INTERVAL:
        return jsonify(error='Espera unos segundos antes de leer otra foto.'), 429
    session['scan_at'] = now
    upload = request.files.get('photo')
    if upload is None or not upload.filename:
        return jsonify(error='Selecciona la foto de tu consigna.'), 400
    usage = []
    try:
        return jsonify(**extract_assignment(upload, usage_sink=usage))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except GenerationError as exc:
        return jsonify(error=exc.user_message), 502
    finally:
        # Los tokens de la lectura se suman al informe que se genere después.
        session['scan_extraction_tokens'] = session.get('scan_extraction_tokens', 0) + sum(usage)


@app.errorhandler(413)
def upload_too_large(error):
    if request.path in ('/glossary/terms', '/report/scan'):
        return jsonify(error='El archivo supera el límite de 10 MB.'), 413
    return form_error('El archivo o el formulario es demasiado grande (máximo 10 MB por archivo).')

class JobFailed(Exception):
    """La generación no pudo completarse; el mensaje es lo que se le muestra al usuario."""


def _stage(token, stage, started):
    """Etapa que ve el usuario en la página de espera; queda también en el registro con su tiempo,
    para saber dónde se va el tiempo cuando una generación tarda."""
    db.set_job_stage(token, stage)
    logging.info('Generación %s: etapa "%s" a los %.1f s', token[:8], stage, time.time() - started)


@app.route('/process_form', methods=['POST'])
@login_required
@rate_limit.rate_limit(10, 60, key_func=rate_limit.per_user)
@plans.with_generation_quota
def process_form():
    """Valida lo barato y deja la generación corriendo en segundo plano (ver jobs.py): la petición
    termina al instante, así ningún proxy la corta por lenta que sea la IA."""
    user = current_user()

    form_data = request.form
    institution = form_data.get('instituto', 'universidad')
    if institution not in ('universidad', 'bachiller'):
        return form_error('Elige universidad o bachillerato.')
    document_kind = form_data.get('document_kind', 'report')
    if document_kind not in ('report', 'glossary'):
        return form_error('Elige trabajo normal o glosario.')
    is_glossary = document_kind == 'glossary'
    manual_mode = not is_glossary and form_data.get('global-mode') == 'standard'

    # Se valida ANTES de llamar a la IA: un usuario sin permiso no debe
    # gastar tokens. Con IA y manual hay reglas distintas (ver plans.py).
    allowed, reason = plans.generation_access(user, 'manual' if manual_mode else 'ai')
    if not allowed:
        return plans.redirect_to_plans(reason)

    # El título sale en la portada en ambos modos, y en modo IA además es el
    # tema a investigar: sin uno válido no hay nada que generar.
    title_error = validate_title_field(form_data.get('title'))
    if title_error:
        return form_error(title_error)

    # Un documento a la vez por usuario: un segundo envío (o recargar) vuelve al avance del que ya corre.
    db.reap_stale_jobs()
    running = db.active_job(user['id'])
    if running is not None:
        return redirect(url_for('generating', token=running['token']))
    if not jobs.try_acquire():
        return form_error('Hay muchos documentos generándose en este momento. Inténtalo de nuevo en un minuto.')

    try:
        inputs = {
            'form': form_data.to_dict(flat=True),
            'document_type': 'bach' if institution == 'bachiller' else 'uni',
            'document_kind': document_kind,
            # Lo guardado en la sesión se toma aquí: el hilo de fondo no tiene sesión.
            'scan_tokens': session.pop('scan_extraction_tokens', 0),    # lectura de foto previa, si hubo
            'glossary_tokens': session.pop('glossary_extraction_tokens', 0) if is_glossary else 0,
            'prevalidated': session.pop('validated_title', {}),
        }
        ticket = g.generation_ticket
        token = db.create_job(user['id'], document_kind, ticket)
        ticket['handed_off'] = True            # el trabajo cierra el cupo (lo consume o lo devuelve)
    except Exception:
        jobs.release()
        raise
    job_ticket = db.job_ticket(ticket)
    jobs.start(app, token, lambda: _run_generation(token, user['id'], inputs, job_ticket))
    return redirect(url_for('generating', token=token))


def _run_generation(token, user_id, inputs, ticket):
    """Corre en un hilo, sin petición HTTP: genera el documento y deja el resultado en el trabajo."""
    g.generation_ticket = ticket                 # tope de términos y registro del documento
    ai_provider.generation_trace.set([])
    started = time.time()
    try:
        stem, warnings = _generate_document(token, user_id, inputs, started)
    except JobFailed as exc:
        db.fail_job(token, str(exc), refund=not ticket.get('done'))
        logging.info('Generación %s fallida a los %.1f s: %s', token[:8], time.time() - started, exc)
    except Exception:
        logging.exception('Error inesperado generando el documento (trabajo %s)', token[:8])
        db.fail_job(token, 'Ocurrió un error inesperado generando el documento. Inténtalo de nuevo; '
                           'si se repite, avisa al administrador.', refund=not ticket.get('done'))
    else:
        if db.complete_job(token, stem, warnings):
            logging.info('Generación %s terminada en %.1f s', token[:8], time.time() - started)
        else:
            logging.warning('La generación %s terminó a los %.1f s, pero ya estaba cerrada como interrumpida.',
                            token[:8], time.time() - started)


def _generate_document(token, user_id, inputs, started):
    """El trabajo pesado de process_form. Devuelve (nombre del archivo, avisos) o lanza JobFailed."""
    form_data = inputs['form']
    document_type = inputs['document_type']
    document_kind = inputs['document_kind']
    is_glossary = document_kind == 'glossary'
    manual_mode = not is_glossary and form_data.get('global-mode') == 'standard'

    processor = FormProcessor(form_data, document_type)
    # Mayúsculas y tildes del título y subtítulos (modelo ligero + control de código + JEV).
    # Nunca falla: ante cualquier problema queda la primera letra en mayúscula.
    usage_sink = []
    processor.apply_formatted_texts(
        format_texts(processor.title, processor.subtitles, usage_sink=usage_sink))
    processor.process()
    replacements, head_title = processor.generate_replacements()

    # 'Lo escribo yo': el usuario redacta el contenido a mano en vez de
    # pedírselo a la IA. Estos checkboxes controlan, en ambos modos, si la
    # introducción y la conclusión se incluyen en el documento o no.
    incluir_introduccion = not is_glossary and 'incluir_introduccion' in form_data
    incluir_conclusion = not is_glossary and 'incluir_conclusion' in form_data
    incluir_bibliografia = 'incluir_bibliografia' in form_data

    introduccion = ''
    conclusion = ''
    bibliography = ''
    glossary_entries = None
    # Lista compartida donde cada llamada a la IA anota sus tokens
    # (ver IA._record_usage); se suma al final para guardarla en la BD.
    usage_sink.append(inputs['scan_tokens'])
    prevalidated = inputs['prevalidated']
    title_validated = (prevalidated.get('title') == processor.original_title.strip().casefold()
                       and 0 <= time.time() - prevalidated.get('at', 0) < 300)
    if title_validated:
        usage_sink.append(prevalidated.get('tokens', 0))
    # Avisos que no impiden entregar el documento pero el usuario debe
    # saber (se muestran en la pantalla de descarga).
    warnings = []

    if is_glossary:
        try:
            source = form_data.get('glossary_source', 'topic')
            if source == 'list':
                terms = parse_terms(form_data.get('glossary_terms', ''))
                count = len(terms)
            elif source == 'topic':
                terms = None
                count = int(form_data.get('glossary_count', '20'))
                from glossary import max_terms
                if not 1 <= count <= max_terms():
                    raise ValueError(f'El glosario debe tener entre 1 y {max_terms()} términos.')
            else:
                raise ValueError('Elige el tema o una lista de términos para el glosario.')
            usage_sink.append(inputs['glossary_tokens'])
            _stage(token, 'terms', started)
            check_glossary(processor.title, terms, usage_sink=usage_sink,
                           include_title=not title_validated)
            _stage(token, 'definitions', started)
            glossary_entries = generate_glossary(processor.title, count, terms=terms,
                bibliography=incluir_bibliografia, usage_sink=usage_sink)
            # Las listas grandes por tema ya se validan antes de definirlas;
            # el generador exige después conservar exactamente esa lista.
            if terms is None and count <= 25:
                _stage(token, 'check', started)
                check_glossary(processor.title, [entry['term'] for entry in glossary_entries],
                               usage_sink=usage_sink, include_title=False)
            body = ''
        except ValueError as exc:
            raise JobFailed(str(exc))
        except GenerationError as exc:
            raise JobFailed(f'No se pudo generar el glosario. {exc.user_message}')
    elif manual_mode:
        body = processor.body
        if incluir_introduccion:
            introduccion = processor.introduccion
        if incluir_conclusion:
            conclusion = processor.conclusion
        if incluir_bibliografia:
            bibliography = form_data.get('bibliografia', '').strip()
        if not (body.strip() or introduccion.strip() or conclusion.strip() or bibliography.strip()):
            # Portada sola: permitido en modo manual con todo en blanco.
            warnings.append('El documento tiene solo la portada porque dejaste el contenido en blanco.')
    else:
        # 1) ¿El título es un tema válido? Distinguir "el modelo lo rechazó"
        #    de "no se pudo consultar al modelo": lo segundo NO es culpa del
        #    título y antes se trataba igual (volvía al inicio sin decir nada).
        try:
            if not title_validated:
                _stage(token, 'validate', started)
                verdict = check_title(processor.title, usage_sink=usage_sink)
                if not verdict.valid:
                    raise JobFailed(verdict.message(processor.title))
        except GenerationError as e:
            raise JobFailed(f'No se pudo validar el título. {e.user_message}')

        # 2) Desarrollo del trabajo (con búsqueda en tiempo real si está disponible).
        try:
            _stage(token, 'content', started)
            body = generate_essay_content(processor.title, processor.subtitles,
                                          usage_sink=usage_sink, warnings=warnings)
        except GenerationError as e:
            raise JobFailed(f'No se pudo generar el desarrollo del trabajo. {e.user_message}')

        # 3) La introducción y la conclusión solo dependen del título y del
        #    cuerpo ya generado, no una de la otra, así que se piden en
        #    paralelo en vez de esperar una llamada tras otra a la IA.
        #    Sólo se piden las que el usuario dejó activadas. Si una falla el
        #    documento se entrega igual, avisando cuál faltó y por qué.
        if incluir_introduccion or incluir_conclusion:
            _stage(token, 'sections', started)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}
            if incluir_introduccion:
                futures['introducción'] = executor.submit(
                    copy_context().run, generate_introduction, processor.title, body, usage_sink)
            if incluir_conclusion:
                futures['conclusión'] = executor.submit(
                    copy_context().run, generate_conclusion, processor.title, body, usage_sink)
            results = {}
            for label, future in futures.items():
                try:
                    results[label] = future.result()
                except GenerationError as e:
                    warnings.append(f'No se incluyó la {label}. {e.user_message}')
            introduccion = results.get('introducción', '')
            conclusion = results.get('conclusión', '')

        if incluir_bibliografia:
            try:
                _stage(token, 'bibliography', started)
                bibliography = generate_bibliography(processor.title, body, usage_sink=usage_sink)
            except GenerationError as exc:
                warnings.append(f'No se incluyó la bibliografía. {exc.user_message}')

    _stage(token, 'build', started)
    input_doc = 'input/plantilla_bach.docx' if document_type == 'bach' else 'input/plantilla.docx'
    input_doc2 = 'input/plantilla_bachempty.docx' if document_type == 'bach' else 'input/plantillaempty.docx'
    # Check if the file exists
    file_stem = safe_filename(head_title)
    # Nombre libre en el almacenamiento (dos usuarios pueden tener el mismo título).
    full_stem = storage.unique_stem(file_stem)
    docx_output = f'output/{full_stem}.docx'
    university_name = form_data.get('u', '')
    try:
        Document_process.fill_placeholders(docx_output, input_doc, input_doc2, replacements,
                                            introduccion, body, conclusion, head_title, document_type,
                                            university_name=university_name,
                                            detect_subtitles=not manual_mode,
                                            bibliography=bibliography,
                                            glossary_entries=glossary_entries,
                                            subtitles=processor.subtitles,
                                            font_name='Times New Roman' if form_data.get('fuente') == 'tnr' else 'Arial')
    except Exception:
        logging.exception('Error armando el documento "%s"', head_title)
        raise JobFailed('Ocurrió un error armando el documento. Inténtalo de nuevo; '
                        'si se repite, avisa al administrador.')

    if not os.path.isfile(docx_output):
        raise JobFailed('El documento no se pudo guardar. Inténtalo de nuevo.')
    if not os.path.isfile(docx_output[:-5] + '.pdf'):
        warnings.append('No se pudo generar el PDF; solo está disponible la versión Word.')
    try:
        storage.publish(full_stem)
    except storage.StorageError:
        raise JobFailed('No se pudo guardar el documento. Inténtalo de nuevo.')

    db.record_document(user_id, head_title, document_type, tokens_used=sum(usage_sink),
                       mode='manual' if manual_mode else 'ai',
                       file_stem=full_stem)
    return full_stem, warnings


def _own_job(token):
    """El trabajo si es del usuario con sesión (404 si no). Si su proceso murió lo da por fallido."""
    job = db.get_job(token)
    if job is None or job['user_id'] != current_user()['id']:
        abort(404)
    if job['status'] == 'running':
        db.reap_stale_jobs()
        job = db.get_job(token)
    return job


@app.route('/generating/<token>')
@login_required
def generating(token):
    """Página de espera: muestra el avance real y pasa sola a la descarga (o al error) al terminar."""
    job = _own_job(token)
    if job['status'] != 'running':
        return redirect(url_for('finish_generation', token=token))
    return render_template('generating.html', token=token, kind=job['document_kind'])


@app.route('/generating/<token>/status')
@login_required
@rate_limit.rate_limit(90, 60, key_func=rate_limit.per_user)
def generation_status(token):
    job = _own_job(token)
    response = jsonify(status=job['status'], stage=job['stage'], kind=job['document_kind'],
                       next=url_for('finish_generation', token=token))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/generating/<token>/finish')
@login_required
def finish_generation(token):
    """Termina el flujo de la generación: a la descarga si salió bien, al formulario con el motivo si no."""
    job = _own_job(token)
    if job['status'] == 'running':
        return redirect(url_for('generating', token=token))
    if job['status'] == 'error':
        return form_error(job['message'] or 'No se pudo generar el documento. Inténtalo de nuevo.')
    session['file_generated'] = True
    session['document_kind'] = job['document_kind']
    for text in db.consume_job_warnings(token):
        flash(text, 'warning')
    return redirect(url_for('choose_file', filename=job['result_stem']))


@app.route('/choose_file/<filename>')
@login_required
def choose_file(filename):
    """Renders the file download page."""
    if 'file_generated' not in session:
        # Sesión vencida, o se volvió al formulario (que reinicia la marca).
        return form_error('Esa descarga ya no está disponible en tu sesión. '
                          'Genera el documento de nuevo.')
    doc = db.get_document_by_filename(filename)
    if (doc is None or doc['user_id'] != current_user()['id']
            or db.time_left(doc['expires_at'])[1] <= 0 or not storage.exists(filename, 'docx')):
        # Sin esto se mostraba la pantalla de descarga con enlaces que llevaban a un 404.
        return form_error('El documento ya no existe o no está disponible. Genera el documento de nuevo.')

    # Enlaces públicos temporales para compartir (WhatsApp / Gmail / otros).
    # Van firmados y con caducidad: el destinatario no tiene sesión, así que
    # no puede pasar por /download_file, que exige login.
    token = _share_serializer().dumps(filename)
    share_urls = {
        filetype: url_for('shared_file', token=token, filetype=filetype, _external=True)
        for filetype in document_filetypes(doc)
    }
    return render_template('download.html', filename=filename, share_urls=share_urls,
                           expiry_text=db.time_left(doc['expires_at'])[0],
                           document_kind=session.get('document_kind', 'report'))

@app.route('/download_file/<filename>/<filetype>')
@login_required
@rate_limit.rate_limit(30, 60, key_func=rate_limit.per_ip)
def download_file(filename, filetype):
    if 'file_generated' not in session:
        return form_error('Esa descarga ya no está disponible en tu sesión. '
                          'Genera el documento de nuevo.')
    if filetype not in SHARE_FILETYPES:
        return render_template('404.html'), 404
    doc = db.get_document_by_filename(filename)
    if (doc is None or doc['user_id'] != current_user()['id']
            or db.time_left(doc['expires_at'])[1] <= 0):
        return render_template('404.html'), 410
    if filetype not in document_filetypes(doc):
        return render_template('404.html'), 403
    try:
        return storage.serve(os.path.basename(filename), filetype)
    except Exception as e:
        logging.error('Error descargando archivo: %s', e)
        return render_template('404.html'), 404


@app.route('/my_documents')
@login_required
def my_documents():
    """Informes generados por el usuario que todavía se conservan."""
    documents = []
    page = max(1, request.args.get('page', 1, type=int))
    rows = db.list_user_documents(current_user()['id'], limit=51, offset=(page - 1) * 50)
    available = storage.stems_available()      # una sola consulta al almacenamiento
    for doc in rows[:50]:
        if 'docx' not in available.get(doc['file_stem'], ()):
            continue        # el archivo ya no está (borrado a mano, etc.)
        label, fraction = db.time_left(doc['expires_at'], doc['created_at'])
        documents.append({
            'id': doc['id'], 'title': doc['title'], 'doc_type': doc['doc_type'],
            'mode': doc['mode'], 'created_at': doc['created_at'],
            'time_left': label, 'fraction': fraction,
            'expires_iso': doc['expires_at'].replace(' ', 'T') + 'Z',
            'created_iso': doc['created_at'].replace(' ', 'T') + 'Z',
            'urgent': fraction < 0.1,
            'has_pdf': 'pdf' in document_filetypes(doc) and 'pdf' in available.get(doc['file_stem'], ()),
            'share_urls': {kind: url_for('shared_file',
                token=_share_serializer().dumps(doc['file_stem']), filetype=kind, _external=True)
                for kind in document_filetypes(doc) if kind in available.get(doc['file_stem'], ())},
        })
    return render_template('my_documents.html', documents=documents, page=page, has_next=len(rows) > 50)


@app.route('/my_documents/<int:doc_id>/<filetype>')
@login_required
@rate_limit.rate_limit(30, 60, key_func=rate_limit.per_ip)
def my_document_download(doc_id, filetype):
    """Descarga de un informe propio. Comprueba que sea del usuario y siga vigente."""
    doc = db.get_user_document(current_user()['id'], doc_id)
    if filetype not in SHARE_FILETYPES or doc is None:
        flash('Ese informe ya no está disponible.', 'error')
        return redirect(url_for('my_documents'))
    if filetype not in document_filetypes(doc):
        return render_template('404.html'), 403
    try:
        return storage.serve(doc['file_stem'], filetype,
                             download_name=f"{safe_filename(doc['title'])}.{filetype}")
    except FileNotFoundError:
        flash('Ese archivo ya no está disponible.', 'error')
        return redirect(url_for('my_documents'))


@app.route('/s/<token>/<filetype>')
@rate_limit.rate_limit(30, 60, key_func=rate_limit.per_ip)
def shared_file(token, filetype):
    """Descarga pública mediante enlace firmado (sin login, caduca junto con el archivo)."""
    if filetype not in SHARE_FILETYPES:
        return render_template('404.html'), 404
    try:
        filename = _share_serializer().loads(token)
    except (SignatureExpired, BadSignature):
        return render_template('404.html'), 410
    if not isinstance(filename, str):
        return render_template('404.html'), 410
    doc = db.get_document_by_filename(filename)
    if doc is None or db.time_left(doc['expires_at'])[1] <= 0:
        return render_template('404.html'), 410
    if filetype not in document_filetypes(doc):
        return render_template('404.html'), 403
    try:
        return storage.serve(os.path.basename(filename), filetype)
    except Exception as e:
        logging.error('Error sirviendo archivo compartido: %s', e)
        return render_template('404.html'), 404


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500



logging.info('Base de datos: %s · Archivos: %s',
             'PostgreSQL (Supabase)' if db.is_postgres() else f'SQLite local ({db.DB_PATH})',
             'Cloudflare R2 / S3' if storage.is_remote() else 'carpeta local (output/)')

# Limpieza automática de archivos vencidos (ver retention.py). No arranca bajo
# pytest para que las pruebas no borren nada de output/.
if 'pytest' not in sys.modules:
    retention.start_background_cleanup(app)


if __name__ == '__main__':
    app.run(debug=True)
