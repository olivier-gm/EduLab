from flask import Flask, render_template, request, redirect, url_for, send_file, session
from concurrent.futures import ThreadPoolExecutor
import threading
import os
import logging
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Tiene que ejecutarse ANTES de importar db/auth: ambos leen variables de
# entorno (ADMIN_EMAILS, GOOGLE_CLIENT_ID, etc.) al nivel de módulo, en el
# momento del import, así que si el .env se carga después esas variables
# quedan vacías aunque estén bien puestas en el archivo.
load_dotenv()

from form_processor import FormProcessor
from algorythms import Document_process
from IA import generate_essay_content, generate_introduction, generate_conclusion, validate_titles

import db
from auth import auth_bp, current_user, login_required
from admin import admin_bp
import plans
from plans import plans_bp

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
# Lax: el navegador no manda la cookie de sesión en POST desde otros sitios,
# que es lo que protege las acciones del panel admin de peticiones falsas.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.secret_key = os.getenv('SECRET_KEY', '7f8b9a2c3d4e5f608192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8')

# Enlaces para compartir: firmados con la clave de la app y válidos 2 h (los
# archivos se borran a los ~130 min, así que el enlace no sobrevive al archivo).
SHARE_MAX_AGE = 7200
SHARE_FILETYPES = ('docx', 'pdf')


def _share_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='share-file')


db.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(plans_bp)


@app.context_processor
def inject_current_user():
    return {'current_user': current_user()}


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
    random_code = ''
    if os.path.isfile(f'output/{head_title}.docx'):
        # If the file exists, generate a random code and append it to the filename
        random_code = '_' + Document_process.generate_random_code()
    docx_output = f'output/{head_title}{random_code}.docx'

    university_name = form_data.get('u', '')
    Document_process.fill_placeholders(docx_output, input_doc, input_doc2, replacements,
                                        introduccion, body, conclusion, head_title, 'bach',
                                        university_name=university_name)

    db.record_document(user['id'], head_title, 'bach', tokens_used=0, mode='manual')
    session['file_generated'] = True

    # Redirect to a new page or indicate success
    return redirect(url_for('choose_file', filename=head_title + random_code))
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

    if manual_mode:
        body = processor.body
        if incluir_introduccion:
            introduccion = processor.introduccion
        if incluir_conclusion:
            conclusion = processor.conclusion
    else:
        body = ''
        if validate_titles(processor.formatted_title, usage_sink=usage_sink).startswith('TRUE'):
            body = generate_essay_content(processor.title, processor.subtitles, usage_sink=usage_sink)

        else:

            return redirect(url_for('welcome'))  # Redirect to the form if the file wasn't generated
        if body != '':
            # La introducción y la conclusión solo dependen del título y del
            # cuerpo ya generado, no una de la otra, así que se piden en
            # paralelo en vez de esperar una llamada tras otra a Gemini.
            # Sólo se piden las que el usuario dejó activadas.
            with ThreadPoolExecutor(max_workers=2) as executor:
                intro_future = executor.submit(generate_introduction, processor.title, body, usage_sink) if incluir_introduccion else None
                conclusion_future = executor.submit(generate_conclusion, processor.title, body, usage_sink) if incluir_conclusion else None
                if intro_future:
                    introduccion = intro_future.result()
                if conclusion_future:
                    conclusion = conclusion_future.result()

    input_doc='input/plantilla.docx'
    input_doc2='input/plantillaempty.docx'
    # Check if the file exists
    random_code = ''
    if os.path.isfile(f'output/{head_title}.docx'):
        # If the file exists, generate a random code and append it to the filename
        random_code = '_' + Document_process.generate_random_code()
    docx_output = f'output/{head_title}{random_code}.docx'
    university_name = form_data.get('u', '')
    Document_process.fill_placeholders(docx_output, input_doc, input_doc2, replacements,
                                        introduccion, body, conclusion, head_title, 'uni',
                                        university_name=university_name,
                                        detect_subtitles=not manual_mode)

    db.record_document(user['id'], head_title, 'uni', tokens_used=sum(usage_sink),
                       mode='manual' if manual_mode else 'ai')
    session['file_generated'] = True

    # Redirect to a new page or indicate success
    return redirect(url_for('choose_file', filename=head_title + random_code))
    #return redirect(url_for('index'))


@app.route('/choose_file/<filename>')
@login_required
def choose_file(filename):
    """Renders the file download page."""
    if 'file_generated' not in session:
        return redirect(url_for('welcome'))  # Redirect to the form if the file wasn't generated
        # Schedule the file removal after a delay
    file_path = f'output/{filename}.docx'
    threading.Timer(7800, Document_process.remove_file, args=[file_path]).start()

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
        return redirect(url_for('welcome'))
    file_path = f'output/{filename}.{filetype}'
    try:
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logging.error('Error descargando archivo: %s', e)
        return render_template('404.html')


@app.route('/s/<token>/<filetype>')
def shared_file(token, filetype):
    """Descarga pública mediante enlace firmado (sin login, caduca a las 2 h)."""
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


if __name__ == '__main__':
    app.run(debug=True)
