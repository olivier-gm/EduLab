from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import os
import logging
import re
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Tiene que ejecutarse ANTES de importar db/auth: ambos leen variables de
# entorno (ADMIN_EMAILS, GOOGLE_CLIENT_ID, etc.) al nivel de módulo, en el
# momento del import, así que si el .env se carga después esas variables
# quedan vacías aunque estén bien puestas en el archivo.
load_dotenv()

from form_processor import FormProcessor
from algorythms import Document_process
from IA import (generate_essay_content, generate_introduction, generate_conclusion,
                check_title, GenerationError)

import db
from auth import auth_bp, current_user, login_required
from admin import admin_bp
import plans
import retention
from plans import plans_bp

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
# Lax: el navegador no manda la cookie de sesión en POST desde otros sitios,
# que es lo que protege las acciones del panel admin de peticiones falsas.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.secret_key = os.getenv('SECRET_KEY', '7f8b9a2c3d4e5f608192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8')

# Enlaces para compartir: firmados con la clave de la app y válidos lo mismo que
# se conserva el archivo (db.FILE_RETENTION_HOURS), así el enlace no sobrevive
# al archivo.
SHARE_MAX_AGE = db.FILE_RETENTION_HOURS * 3600
SHARE_FILETYPES = ('docx', 'pdf')


def _share_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='share-file')


db.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(plans_bp)


@app.context_processor
def inject_current_user():
    return {'current_user': current_user(), 'retention_hours': db.FILE_RETENTION_HOURS}


# ── Validaciones de la generación ─────────────────────────────────────

TITLE_MIN_LEN = 5      # igual que la validación del formulario (builder.js)
TITLE_MAX_LEN = 300    # maxlength del campo título
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_filename(name):
    """Nombre de archivo seguro a partir del título: sin barras, dos puntos,
    comillas ni otros caracteres que rompían la ruta de salida o creaban
    subcarpetas, y de largo acotado."""
    cleaned = _FILENAME_BAD.sub('_', name).strip(' ._')[:80].strip(' ._')
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


@app.route('/')
def welcome():
    return render_template('main_page.html')

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

    input_doc='input/plantilla_bach.docx'
    input_doc2='input/plantilla_bachempty.docx'

  # Check if the file exists
    file_stem = safe_filename(head_title)
    random_code = ''
    if os.path.isfile(f'output/{file_stem}.docx'):
        # If the file exists, generate a random code and append it to the filename
        random_code = '_' + Document_process.generate_random_code()
    docx_output = f'output/{file_stem}{random_code}.docx'

    university_name = form_data.get('u', '')
    try:
        Document_process.fill_placeholders(docx_output, input_doc, input_doc2, replacements,
                                            introduccion, body, conclusion, head_title, 'bach',
                                            university_name=university_name)
    except Exception:
        logging.exception('Error armando el documento "%s"', head_title)
        return form_error('Ocurrió un error armando el documento. Inténtalo de nuevo; '
                          'si se repite, avisa al administrador.', 'show_form_bach')

    if not os.path.isfile(docx_output):
        return form_error('El documento no se pudo guardar. Inténtalo de nuevo.', 'show_form_bach')
    if not (body.strip() or introduccion.strip() or conclusion.strip()):
        flash('El documento tiene solo la portada porque dejaste el contenido en blanco.', 'warning')
    if not os.path.isfile(docx_output[:-5] + '.pdf'):
        flash('No se pudo generar el PDF; solo está disponible la versión Word.', 'warning')

    db.record_document(user['id'], head_title, 'bach', tokens_used=0, mode='manual',
                       file_stem=file_stem + random_code)
    session['file_generated'] = True

    # Redirect to a new page or indicate success
    return redirect(url_for('choose_file', filename=file_stem + random_code))
    #return redirect(url_for('index'))

@app.route('/form')
@login_required
def show_form():
    session.pop('file_generated', None)
    access = plans.access_summary(current_user())
    if not access['ai']['ok'] and not access['manual']['ok']:
        # Ningún modo disponible: no tiene sentido mostrar el formulario.
        return plans.redirect_to_plans(access['ai']['reason'])
    return render_template('universitario.html', access=access)

@app.route('/process_form', methods=['POST'])
@login_required
def process_form():
    user = current_user()

    form_data = request.form
    manual_mode = form_data.get('global-mode') == 'standard'

    # Se valida ANTES de llamar a Gemini: un usuario sin permiso no debe
    # gastar tokens. Con IA y manual hay reglas distintas (ver plans.py).
    allowed, reason = plans.generation_access(user, 'manual' if manual_mode else 'ai')
    if not allowed:
        return plans.redirect_to_plans(reason)

    # El título sale en la portada en ambos modos, y en modo IA además es el
    # tema a investigar: sin uno válido no hay nada que generar.
    title_error = validate_title_field(form_data.get('title'))
    if title_error:
        return form_error(title_error)

    processor = FormProcessor(form_data, 'uni')
    processor.process()
    replacements, head_title = processor.generate_replacements()

    # 'Lo escribo yo': el usuario redacta el contenido a mano en vez de
    # pedírselo a la IA. Estos checkboxes controlan, en ambos modos, si la
    # introducción y la conclusión se incluyen en el documento o no.
    incluir_introduccion = 'incluir_introduccion' in form_data
    incluir_conclusion = 'incluir_conclusion' in form_data

    introduccion = ''
    conclusion = ''
    # Lista compartida donde cada llamada a Gemini anota sus tokens
    # (ver IA._record_usage); se suma al final para guardarla en la BD.
    usage_sink = []
    # Avisos que no impiden entregar el documento pero el usuario debe
    # saber (se muestran en la pantalla de descarga).
    warnings = []

    if manual_mode:
        body = processor.body
        if incluir_introduccion:
            introduccion = processor.introduccion
        if incluir_conclusion:
            conclusion = processor.conclusion
        if not (body.strip() or introduccion.strip() or conclusion.strip()):
            # Portada sola: permitido en modo manual con todo en blanco.
            warnings.append('El documento tiene solo la portada porque dejaste el contenido en blanco.')
    else:
        # 1) ¿El título es un tema válido? Distinguir "el modelo lo rechazó"
        #    de "no se pudo consultar al modelo": lo segundo NO es culpa del
        #    título y antes se trataba igual (volvía al inicio sin decir nada).
        try:
            if not check_title(processor.formatted_title, usage_sink=usage_sink):
                return form_error(
                    f'La IA no reconoce «{processor.title}» como un tema que pueda investigar '
                    '(parece texto sin sentido o letras al azar). Escríbelo con palabras claras.')
        except GenerationError as e:
            return form_error(f'No se pudo validar el título. {e.user_message}')

        # 2) Desarrollo del trabajo (con búsqueda en tiempo real si está disponible).
        try:
            body = generate_essay_content(processor.title, processor.subtitles,
                                          usage_sink=usage_sink, warnings=warnings)
        except GenerationError as e:
            return form_error(f'No se pudo generar el desarrollo del trabajo. {e.user_message}')

        # 3) La introducción y la conclusión solo dependen del título y del
        #    cuerpo ya generado, no una de la otra, así que se piden en
        #    paralelo en vez de esperar una llamada tras otra a Gemini.
        #    Sólo se piden las que el usuario dejó activadas. Si una falla el
        #    documento se entrega igual, avisando cuál faltó y por qué.
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}
            if incluir_introduccion:
                futures['introducción'] = executor.submit(
                    generate_introduction, processor.title, body, usage_sink)
            if incluir_conclusion:
                futures['conclusión'] = executor.submit(
                    generate_conclusion, processor.title, body, usage_sink)
            results = {}
            for label, future in futures.items():
                try:
                    results[label] = future.result()
                except GenerationError as e:
                    warnings.append(f'No se incluyó la {label}. {e.user_message}')
            introduccion = results.get('introducción', '')
            conclusion = results.get('conclusión', '')

    input_doc='input/plantilla.docx'
    input_doc2='input/plantillaempty.docx'
    # Check if the file exists
    file_stem = safe_filename(head_title)
    random_code = ''
    if os.path.isfile(f'output/{file_stem}.docx'):
        # If the file exists, generate a random code and append it to the filename
        random_code = '_' + Document_process.generate_random_code()
    docx_output = f'output/{file_stem}{random_code}.docx'
    university_name = form_data.get('u', '')
    try:
        Document_process.fill_placeholders(docx_output, input_doc, input_doc2, replacements,
                                            introduccion, body, conclusion, head_title, 'uni',
                                            university_name=university_name,
                                            detect_subtitles=not manual_mode)
    except Exception:
        logging.exception('Error armando el documento "%s"', head_title)
        return form_error('Ocurrió un error armando el documento. Inténtalo de nuevo; '
                          'si se repite, avisa al administrador.')

    if not os.path.isfile(docx_output):
        return form_error('El documento no se pudo guardar. Inténtalo de nuevo.')
    if not os.path.isfile(docx_output[:-5] + '.pdf'):
        warnings.append('No se pudo generar el PDF; solo está disponible la versión Word.')

    db.record_document(user['id'], head_title, 'uni', tokens_used=sum(usage_sink),
                       mode='manual' if manual_mode else 'ai',
                       file_stem=file_stem + random_code)
    session['file_generated'] = True
    for text in warnings:
        flash(text, 'warning')

    # Redirect to a new page or indicate success
    return redirect(url_for('choose_file', filename=file_stem + random_code))


@app.route('/choose_file/<filename>')
@login_required
def choose_file(filename):
    """Renders the file download page."""
    if 'file_generated' not in session:
        # Sesión vencida, o se volvió al formulario (que reinicia la marca).
        return form_error('Esa descarga ya no está disponible en tu sesión. '
                          'Genera el documento de nuevo.')
    file_path = f'output/{filename}.docx'
    if not os.path.isfile(file_path):
        # Sin esto se mostraba la pantalla de descarga con enlaces que llevaban a un 404.
        return form_error(f'El documento ya no existe (los archivos se conservan '
                          f'{db.FILE_RETENTION_HOURS} horas). Genera el documento de nuevo.')

    # Enlaces públicos temporales para compartir (WhatsApp / Gmail / otros).
    # Van firmados y con caducidad: el destinatario no tiene sesión, así que
    # no puede pasar por /download_file, que exige login.
    token = _share_serializer().dumps(filename)
    share_urls = {
        filetype: url_for('shared_file', token=token, filetype=filetype, _external=True)
        for filetype in SHARE_FILETYPES
    }
    return render_template('download.html', filename=filename, share_urls=share_urls)

@app.route('/download_file/<filename>/<filetype>')
@login_required
def download_file(filename, filetype):
    if 'file_generated' not in session:
        return form_error('Esa descarga ya no está disponible en tu sesión. '
                          'Genera el documento de nuevo.')
    if filetype not in SHARE_FILETYPES:
        return render_template('404.html'), 404
    file_path = f'output/{os.path.basename(filename)}.{filetype}'
    try:
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logging.error('Error descargando archivo: %s', e)
        return render_template('404.html')


@app.route('/my_documents')
@login_required
def my_documents():
    """Informes generados por el usuario que todavía se conservan."""
    documents = []
    for doc in db.list_user_documents(current_user()['id']):
        if not os.path.isfile(f"output/{doc['file_stem']}.docx"):
            continue        # el archivo ya no está (borrado a mano, etc.)
        label, fraction = db.time_left(doc['expires_at'])
        documents.append({
            'id': doc['id'], 'title': doc['title'], 'doc_type': doc['doc_type'],
            'mode': doc['mode'], 'created_at': doc['created_at'],
            'time_left': label, 'fraction': fraction,
            'expires_iso': doc['expires_at'].replace(' ', 'T') + 'Z',
            'urgent': fraction < 0.1,
            'has_pdf': os.path.isfile(f"output/{doc['file_stem']}.pdf"),
        })
    return render_template('my_documents.html', documents=documents)


@app.route('/my_documents/<int:doc_id>/<filetype>')
@login_required
def my_document_download(doc_id, filetype):
    """Descarga de un informe propio. Comprueba que sea del usuario y siga vigente."""
    doc = db.get_user_document(current_user()['id'], doc_id)
    if filetype not in SHARE_FILETYPES or doc is None:
        flash('Ese informe ya no está disponible.', 'error')
        return redirect(url_for('my_documents'))
    path = f"output/{doc['file_stem']}.{filetype}"
    if not os.path.isfile(path):
        flash('Ese archivo ya no está disponible.', 'error')
        return redirect(url_for('my_documents'))
    return send_file(path, as_attachment=True, download_name=f"{safe_filename(doc['title'])}.{filetype}")


@app.route('/s/<token>/<filetype>')
def shared_file(token, filetype):
    """Descarga pública mediante enlace firmado (sin login, caduca junto con el archivo)."""
    if filetype not in SHARE_FILETYPES:
        return render_template('404.html'), 404
    try:
        filename = _share_serializer().loads(token, max_age=SHARE_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return render_template('404.html'), 410
    file_path = f'output/{os.path.basename(filename)}.{filetype}'
    try:
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logging.error('Error sirviendo archivo compartido: %s', e)
        return render_template('404.html'), 404


@app.errorhandler(404)
def page_not_found(e):
    # note that we set the 404 status explicitly
    return render_template('404.html')



# Limpieza automática de archivos vencidos (ver retention.py). No arranca bajo
# pytest para que las pruebas no borren nada de output/.
if 'pytest' not in sys.modules:
    retention.start_background_cleanup()


if __name__ == '__main__':
    app.run(debug=True)
