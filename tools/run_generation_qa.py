"""Pruebas reales del flujo HTTP de generación. Nunca escribe en BD/R2 de producción.

Ejecutar con .venv/Scripts/python.exe tools/run_generation_qa.py --resume
Resultados permanentes en qa_generaciones/2026-10-05; cada caso tiene solicitud y respuestas.
"""
import argparse
import io
import itertools
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
ARTIFACTS = ROOT / 'qa_generaciones' / '2026-10-05'
BUNDLED_PYTHON = Path(os.environ.get('QA_PYTHON', 'C:/Users/Usuario/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def setup():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    import db
    from flask import Flask
    # Las reanudaciones conservan la configuración tomada al inicio de la tanda.
    # Evita volver a consultar producción y mezclar ajustes distintos entre casos.
    reuse_snapshot = (ARTIFACTS / 'qa.db').exists()
    if reuse_snapshot:
        db.DATABASE_URL = ''
        db.DB_PATH = str(ARTIFACTS / 'qa.db')
    # Leer únicamente la configuración; no inicializar la app en producción.
    probe = Flask('qa_settings', instance_path=str(ROOT / 'instance'))
    probe.secret_key = os.environ.get('SECRET_KEY', 'qa-read-only')
    with probe.app_context():
        try:
            snapshot = dict(db.get_settings())
        finally:
            db.close_db()
    db.reset_pool()
    db.DATABASE_URL = ''
    db.DB_PATH = str(ARTIFACTS / 'qa.db')
    os.environ['DATABASE_URL'] = ''
    os.environ['DATABASE_PATH'] = db.DB_PATH
    os.environ['RATE_LIMIT_ENABLED'] = '0'
    for key in ('BINANCE_VERIFY_TOKEN', 'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'S3_ENDPOINT_URL'):
        os.environ[key] = ''
    # Suprime únicamente el hilo de limpieza; no activa mocks de modelos.
    import pytest  # noqa: F401
    import app as module
    import ai_provider
    import IA
    module.app.config.update(TESTING=True)
    with module.app.app_context():
        db.set_settings(snapshot)
        user = db.get_user_by_email('qa-generacion@example.invalid')
        uid = user['id'] if user else db.create_user('qa-generacion@example.invalid', 'Pruebas de generación')
        db.get_db().execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
        db.get_db().commit()
        provider, model, key = ai_provider.configuration(IA.MODEL_NAME)
        if not key:
            raise RuntimeError('El proveedor activo no tiene clave configurada.')
        save(ARTIFACTS / 'configuracion_publica.json', {
            'provider': provider, 'model': model, 'search_enabled': ai_provider.search_enabled(),
            'fallback_enabled': snapshot.get('fallback_enabled'), 'minimum_seconds_between_generations': 35,
            'database': 'SQLite aislada', 'storage': 'Local aislado; R2 desactivado',
            'read_production_settings_only': True,
            'reuse_settings_snapshot': reuse_snapshot,
        })
    return module, uid


TITLES = [
    'el puma', 'craneo', 'la vida de jose antonio paez', 'la historia de la ucv',
    'El ciclo de Krebs', 'La Segunda Guerra Mundial y la ONU',
    'Prevención del cáncer de cuello uterino', 'La anatomía del aparato reproductor humano',
    'La fotosíntesis y el equilibrio de los ecosistemas', 'La biodiversidad de Venezuela',
    'El sistema nervioso central', 'La energía solar y sus aplicaciones',
    'La Revolución Francesa', 'El ciclo del agua', 'La inteligencia artificial en la educación',
    'La microbiología y la prevención de infecciones',
]
TERMS = ['Átomo', 'Célula', 'ADN', 'ARN', 'Ósmosis', 'Difusión', 'Enzima', 'Mitosis', 'Meiosis', 'Tejido']
MEDICAL_TERMS = ['Anatomía', 'Arteria', 'Articulación', 'Bacteria', 'Bronquio', 'Capilar', 'Cartílago',
    'Célula', 'Cerebelo', 'Cerebro', 'Corazón', 'Dermis', 'Epitelio', 'Esófago', 'Fémur', 'Glándula',
    'Hemoglobina', 'Hígado', 'Hueso', 'Laringe', 'Ligamento', 'Músculo', 'Neurona', 'Patología',
    'Pulmón', 'Riñón', 'Tendón', 'Tejido', 'Tráquea', 'Vena']


def cover(index, institution):
    count = (1, 2, 8)[index % 3]
    university = ('Universidad Central de Venezuela', 'Universidad de Los Andes', 'Universidad Simón Bolívar')[index % 3]
    data = {'instituto': institution, 'u': university if institution == 'universidad' else 'Liceo Nacional Simón Bolívar',
            'title': TITLES[index % len(TITLES)], 'area': 'Ciencias', 'carrera': 'Educación',
            'asignatura': 'Ciencias naturales', 'teacher': 'María González', 'academico': 'Semestre' if institution == 'universidad' else 'Año',
            'periodo': '3' if institution == 'universidad' else '5', 'seccion': 'A', 'city': 'Caracas', 'date': '05/10/2026',
            'gblock-template-canvas-integrantes': str(count)}
    for number in range(1, count + 1):
        data[f'input{number}'] = f'Estudiante de Prueba {number}'
        data[f'id{number}'] = str(20000000 + number)
    return data


def flags(data, intro, conclusion, bibliography):
    for key, enabled in [('incluir_introduccion', intro), ('incluir_conclusion', conclusion), ('incluir_bibliografia', bibliography)]:
        if enabled:
            data[key] = '1'


def cases():
    result = []
    for index, (institution, subtitles, intro, conclusion, bibliography) in enumerate(itertools.product(
            ['universidad', 'bachiller'], [False, True], [False, True], [False, True], [False, True])):
        data = cover(index, institution)
        data.update({'document_kind': 'report', 'global-mode': 'ia'})
        flags(data, intro, conclusion, bibliography)
        if subtitles:
            data.update({'subtitle_1': 'que es', 'subtitle_2': 'caracteristicas principales', 'subtitle_3': 'importancia y aplicaciones'})
        result.append({'id': f'R{index+1:02}', 'form': data})
    for index, (institution, intro, conclusion, bibliography) in enumerate(itertools.product(
            ['universidad', 'bachiller'], [False, True], [False, True], [False, True])):
        data = cover(index, institution)
        data.update({'document_kind': 'report', 'global-mode': 'standard',
            'title': 'La educación ambiental en Venezuela',
            'body': 'La educación ambiental\n\nLa educación ambiental permite comprender las relaciones entre las personas y el entorno. '
                    'Su aplicación en las escuelas incluye observar el consumo de agua y energía, organizar la separación de residuos '
                    'y cuidar los espacios verdes. Las actividades deben adaptarse a las condiciones de cada comunidad.\n\n'
                    'El aprendizaje se fortalece cuando los estudiantes registran sus observaciones, comparan resultados y proponen mejoras. '
                    'La participación de docentes y familias facilita mantener las acciones durante el año escolar.',
            'introduccion': 'INTRODUCCION_MANUAL. Este informe presenta prácticas de educación ambiental en Venezuela.',
            'conclusion': 'CONCLUSION_MANUAL. La continuidad de las acciones y la participación comunitaria favorecen el aprendizaje.',
            'bibliografia': 'UNESCO. (2020). *Educación para el desarrollo sostenible: hoja de ruta*. UNESCO.\nhttps://unesdoc.unesco.org/ark:/48223/pf0000374896'})
        flags(data, intro, conclusion, bibliography)
        result.append({'id': f'M{index+1:02}', 'form': data})
    sources = ['topic', 'list', 'txt', 'docx', 'pdf', 'scan.pdf', 'png', 'jpg', 'webp']
    for index, (institution, source, bibliography) in enumerate(itertools.product(['universidad', 'bachiller'], sources, [False, True])):
        data = cover(index, institution)
        data.update({'document_kind': 'glossary', 'global-mode': 'ia', 'title': 'Biología celular',
                     'glossary_source': 'topic' if source == 'topic' else 'list', 'glossary_count': '10'})
        if source != 'topic':
            data['glossary_terms'] = '\n'.join(TERMS)
        flags(data, False, False, bibliography)
        case = {'id': f'G{index+1:02}', 'form': data, 'expected_count': 10}
        if source not in ('topic', 'list'):
            case['upload'] = f'asignacion.{source}'
        result.append(case)
    for index, (count, bibliography) in enumerate(itertools.product([1, 25, 26, 100, 300], [False, True])):
        data = cover(index, 'universidad')
        source = 'list' if count in (25, 26) else 'topic'
        data.update({'document_kind': 'glossary', 'global-mode': 'ia', 'title': 'Terminología médica y anatomía humana',
                     'glossary_source': source, 'glossary_count': str(count)})
        if source == 'list':
            data['glossary_terms'] = '\n'.join(MEDICAL_TERMS[:count])
        flags(data, False, False, bibliography)
        result.append({'id': f'B{index+1:02}', 'form': data, 'expected_count': count})
    result.extend([
        {'id': 'C01', 'form': {'title': 'Portada sin contenido', 'u': 'Institución sin logo', 'global-mode': 'standard'}},
        {'id': 'C02', 'form': {**cover(2, 'universidad'), 'title': ('Análisis de la prevención de enfermedades y la promoción de hábitos saludables en estudiantes '
            'de instituciones educativas venezolanas mediante la participación de docentes familias y comunidades durante el año escolar'),
            'document_kind': 'report', 'global-mode': 'standard', 'body': 'Una portada completa con un título largo y ocho estudiantes.'}},
        {'id': 'C03', 'form': {**cover(0, 'bachiller'), 'title': 'Órganos, tejidos y células: análisis de sus funciones', 'global-mode': 'standard',
            'body': 'Los órganos están formados por tejidos, que a su vez están constituidos por células.'}},
    ])
    for index, (intro, conclusion, bibliography) in enumerate(itertools.product([False, True], repeat=3)):
        data = cover(index, 'bachiller')
        data.update({'Uu': 'Unidad Educativa', 'estado': 'Distrito Capital', 'title': 'La fotosíntesis en las plantas',
                     'body': 'Las plantas utilizan la energía de la luz para producir sustancias orgánicas a partir de agua y dióxido de carbono.',
                     'introduccion': 'INTRODUCCION_MANUAL. Este informe explica la fotosíntesis.' if intro else '',
                     'conclusion': 'CONCLUSION_MANUAL. La fotosíntesis es esencial para los ecosistemas.' if conclusion else '',
                     'bibliografia': 'OpenStax. (2018). *Biology 2e*. Rice University.'})
        flags(data, intro, conclusion, bibliography)
        result.append({'id': f'L{index+1:02}', 'endpoint': '/process_form_bach', 'form': data})
    for index, (institution, kind, mode) in enumerate([
            ('universidad', 'report', 'standard'), ('bachiller', 'report', 'standard'),
            ('universidad', 'report', 'ia'), ('universidad', 'glossary', 'ia')], 1):
        data = cover(0, institution)
        data.update({'title': 'El sistema solar', 'fuente': 'tnr', 'document_kind': kind,
                     'global-mode': mode, 'body': 'El sistema solar está formado por el Sol y los cuerpos que orbitan a su alrededor.',
                     'glossary_source': 'topic', 'glossary_count': '10'})
        result.append({'id': f'F{index:02}', 'form': data, **({'expected_count': 10} if kind == 'glossary' else {})})
    for index, (intro, conclusion, bibliography) in enumerate([(1,0,0), (0,1,0), (0,0,1), (1,1,1)], 4):
        data = dict(next(case['form'] for case in result if case['id'] == 'M01'))
        data['body'] = ''
        flags(data, intro, conclusion, bibliography)
        result.append({'id': f'C{index:02}', 'form': data})
    data = cover(2, 'universidad')
    data.update({'title': 'El sistema nervioso humano', 'document_kind': 'report', 'global-mode': 'ia'})
    for index, subtitle in enumerate(['organizacion general', 'cerebro', 'cerebelo', 'tronco encefalico',
            'medula espinal', 'neuronas', 'sinapsis', 'enfermedades neurologicas'], 1):
        data[f'subtitle_{index}'] = subtitle
    flags(data, True, True, True)
    result.append({'id': 'C08', 'form': data})
    clinical_terms = ['Pene', 'Vagina', 'Vulva', 'Necrosis', 'Hemorragia', 'Biopsia',
                      'Menstruación', 'Parto', 'Ovulación', 'Sepsis', 'Autopsia', 'Placenta']
    for index, bibliography in enumerate([False, True], 1):
        data = cover(0, 'universidad')
        data.update({'title': 'Anatomía y patología: terminología médica educativa',
                     'document_kind': 'glossary', 'global-mode': 'ia', 'glossary_source': 'list',
                     'glossary_count': str(len(clinical_terms)), 'glossary_terms': '\n'.join(clinical_terms)})
        flags(data, False, False, bibliography)
        result.append({'id': f'E{index:02}', 'form': data, 'expected_count': len(clinical_terms)})
    data = cover(0, 'universidad')
    data.update({'title': 'Biología celular', 'document_kind': 'glossary', 'global-mode': 'ia',
                 'glossary_source': 'list', 'glossary_count': '1', 'glossary_terms': 'Zeiosis'})
    result.append({'id': 'E03', 'form': data, 'expected_count': 1})
    for index, suffix in enumerate(['png', 'jpg', 'webp'], 1):
        data = cover(0, 'universidad')
        data.update({'title': 'El ciclo del agua', 'document_kind': 'report', 'global-mode': 'ia'})
        flags(data, True, True, index == 3)
        result.append({'id': f'S{index:02}', 'form': data, 'scan': f'informe.{suffix}'})
    # Casos representativos primero, para detectar problemas sin recorrer toda la matriz.
    priority = ['M01', 'M09', 'R01', 'R17', 'G01', 'G19', 'G08', 'B10', 'C02']
    result.sort(key=lambda case: (priority.index(case['id']) if case['id'] in priority else len(priority), case['id']))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--interval', type=float, default=35)
    args = parser.parse_args()
    if args.interval < 35:
        raise SystemExit('El intervalo mínimo es 35 segundos para respetar dos generaciones por minuto.')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    module, uid = setup()
    import ai_provider
    import title_check
    import db
    from docx import Document
    matrix = cases()
    save(ARTIFACTS / 'matriz.json', matrix)
    subprocess.run([str(BUNDLED_PYTHON), str(ROOT / 'tools/inspect_generation_qa.py'), '--fixtures', str(ARTIFACTS / 'entradas')], check=True)
    logging.getLogger().setLevel(logging.ERROR)
    for noisy in ('httpx', 'httpcore', 'google_genai'):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    active = {}
    lock = threading.Lock()
    original_call = ai_provider._call
    original_jev = title_check._call_jev
    original_fill = module.Document_process.fill_placeholders

    def trace_call(provider, model, key, config, contents, values):
        call = {'provider': provider, 'model': model, 'tools': bool(getattr(config, 'tools', None)),
                'contents': str(contents)[:100000], 'system_instruction': str(getattr(config, 'system_instruction', ''))[:100000]}
        # Nunca se incluye la clave, aunque el proveedor la repita en una excepción.
        started = time.monotonic()
        try:
            response = original_call(provider, model, key, config, contents, values)
            import IA
            try:
                call['response'] = IA._extract_text(response)
            except IA.GenerationError as extraction_error:
                call['extraction_error'] = extraction_error.code
            usage = getattr(response, 'usage_metadata', None)
            call['usage'] = usage.model_dump(exclude_none=True) if hasattr(usage, 'model_dump') else {}
            call['sources'] = []
            for candidate in getattr(response, 'candidates', None) or []:
                for chunk in getattr(getattr(candidate, 'grounding_metadata', None), 'grounding_chunks', None) or []:
                    web = getattr(chunk, 'web', None)
                    if web:
                        call['sources'].append({'title': getattr(web, 'title', ''), 'url': getattr(web, 'uri', '')})
            return response
        except Exception as error:
            call['error'] = str(error).replace(key, '[REDACTED]')[:1000]
            raise
        finally:
            call['seconds'] = round(time.monotonic() - started, 2)
            with lock:
                active.setdefault('model_calls', []).append(call)
                if active.get('case_directory'):
                    save(Path(active['case_directory']) / 'respuestas.json', {k: v for k, v in active.items() if k != 'case_directory'})

    def trace_jev(state, questions=None):
        call = {'state': state, 'questions': questions}
        started = time.monotonic()
        try:
            response = original_jev(state, questions)
            call['response'] = response
            return response
        except Exception as error:
            call['error'] = type(error).__name__ + ': ' + str(error)[:200]
            raise
        finally:
            call['seconds'] = round(time.monotonic() - started, 2)
            active.setdefault('jev_calls', []).append(call)

    def capture_fill(*positional, **keywords):
        active['content'] = {'introduction': positional[4], 'body': positional[5], 'conclusion': positional[6],
            'head_title': positional[7], 'template': positional[1], 'bibliography': keywords.get('bibliography', ''),
            'glossary_entries': keywords.get('glossary_entries'), 'subtitles': keywords.get('subtitles', [])}
        return original_fill(*positional, **keywords)

    ai_provider._call = trace_call
    title_check._call_jev = trace_jev
    module.Document_process.fill_placeholders = staticmethod(capture_fill)
    with module.app.test_client() as client:
        with client.session_transaction() as session:
            session['user_id'] = uid
            session['auth_version'] = 0
        for case in matrix:
            if args.only and case['id'] not in args.only.split(','):
                continue
            directory = ARTIFACTS / case['id']
            result_file = directory / 'resultado.json'
            if args.resume and result_file.exists():
                continue
            # El último inicio se conserva entre ejecuciones, incluso tras un fallo.
            stamp = ARTIFACTS / 'ultimo_inicio.txt'
            remaining = args.interval - (time.time() - float(stamp.read_text() if stamp.exists() else 0))
            if remaining > 0:
                time.sleep(remaining)
            stamp.write_text(str(time.time()))
            directory.mkdir(parents=True, exist_ok=True)
            save(directory / 'solicitud.json', case)
            active.clear()
            active['case_directory'] = str(directory)
            print(json.dumps({'starting': case['id'], 'title': case['form']['title']}, ensure_ascii=False), flush=True)
            started = time.monotonic()
            form = dict(case['form'])
            result = {'id': case['id'], 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'failed'}
            try:
                if case.get('upload'):
                    upload = ARTIFACTS / 'entradas' / case['upload']
                    response = client.post('/glossary/terms', data={'terms_file': (io.BytesIO(upload.read_bytes()), upload.name)}, content_type='multipart/form-data')
                    extracted = response.get_json()
                    save(directory / 'extraccion.json', extracted)
                    if response.status_code != 200:
                        raise RuntimeError(f'Extracción: {extracted}')
                    if set(extracted['terms']) != set(TERMS):
                        result.setdefault('issues', []).append('La extracción no coincide con la lista completa de términos.')
                    form['glossary_terms'] = '\n'.join(extracted['terms'])
                if case.get('scan'):
                    upload = ARTIFACTS / 'entradas' / case['scan']
                    response = client.post('/report/scan', data={'photo': (io.BytesIO(upload.read_bytes()), upload.name)}, content_type='multipart/form-data')
                    extracted = response.get_json()
                    save(directory / 'extraccion.json', extracted)
                    if response.status_code != 200:
                        raise RuntimeError(f'Lectura de la consigna: {extracted}')
                    form['title'] = extracted['title']
                    for index, topic in enumerate(extracted['topics'], 1):
                        form[f'subtitle_{index}'] = topic
                save(directory / 'formulario_enviado.json', form)
                with module.app.app_context():
                    previous_ids = {document['id'] for document in db.list_user_documents(uid)}
                response = client.post(case.get('endpoint', '/process_form'), data=form)
                result['response_status'] = response.status_code
                result['redirect'] = response.headers.get('Location', '')
                with client.session_transaction() as session:
                    result['messages'] = session.pop('_flashes', [])
                if '/choose-file/' not in result['redirect'] and '/choose_file/' not in result['redirect']:
                    # El nombre exacto de la ruta no importa si se registró un archivo real.
                    with module.app.app_context():
                        records = db.list_user_documents(uid)
                    if not records or records[0]['title'] != active.get('content', {}).get('head_title'):
                        raise RuntimeError(f'No se entregó documento: {result["redirect"]} {result["messages"]}')
                with module.app.app_context():
                    new_documents = [document for document in db.list_user_documents(uid) if document['id'] not in previous_ids]
                if len(new_documents) != 1:
                    raise RuntimeError(f'No se registró exactamente un documento nuevo: {result["messages"]}')
                document = new_documents[0]
                result['tokens'] = document['tokens_used']
                result['title'] = document['title']
                if 'incluir_bibliografia' in form:
                    if form.get('global-mode') == 'standard' or case['id'].startswith('L'):
                        result['bibliography'] = {'bibliography_source': 'manual', 'bibliography_reason': 'Texto aportado por el usuario.'}
                    else:
                        with module.app.app_context():
                            settings = db.get_settings()
                            result['bibliography'] = {key: settings.get(key) for key in (
                                'bibliography_source', 'bibliography_reason', 'bibliography_provider', 'bibliography_model')}
                stem = document['file_stem']
                result['download_checks'] = {}
                if client.get(result['redirect']).status_code != 200:
                    raise RuntimeError('No abre la pantalla de descarga.')
                for extension in ('docx', 'pdf'):
                    source = ROOT / 'output' / f'{stem}.{extension}'
                    if source.exists():
                        shutil.copy2(source, directory / f'documento.{extension}')
                        download = client.get(f'/download_file/{stem}/{extension}')
                        if download.status_code != 200 or download.data != source.read_bytes():
                            raise RuntimeError(f'La descarga de {extension} no coincide con el archivo generado.')
                        result['download_checks'][extension] = 'HTTP 200 y contenido idéntico'
                        download.close()
                if not (directory / 'documento.docx').exists():
                    raise RuntimeError('Falta el Word.')
                save(directory / 'respuestas.json', active)
                subprocess.run([str(BUNDLED_PYTHON), str(ROOT / 'tools/inspect_generation_qa.py'), '--case', str(directory)], check=True)
                inspection = json.loads((directory / 'inspeccion.json').read_text(encoding='utf-8'))
                result.setdefault('issues', []).extend(inspection['issues'])
                result['pages'] = inspection.get('pages')
                result['status'] = 'issues' if result['issues'] else 'passed'
                # Copias de QA permanentes; el output solo contiene estos archivos temporales propios.
                for extension in ('docx', 'pdf'):
                    own_file = ROOT / 'output' / f'{stem}.{extension}'
                    if own_file.exists():
                        own_file.unlink()
            except Exception as error:
                result['error'] = str(error)[:2000]
                save(directory / 'respuestas.json', active)
            result['seconds'] = round(time.monotonic() - started, 2)
            result['model_calls'] = len(active.get('model_calls', []))
            save(result_file, result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            results = [json.loads(path.read_text(encoding='utf-8')) for path in ARTIFACTS.glob('*/resultado.json')]
            save(ARTIFACTS / 'resumen.json', results)
            from report_generation_qa import build
            build()


if __name__ == '__main__':
    main()
