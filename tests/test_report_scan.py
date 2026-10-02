# -*- coding: utf-8 -*-
"""Lectura de la foto de una consigna para los informes (solo imágenes) y su
validación de seguridad, que es la misma de la subida del glosario."""
import io
import os

import pytest

import db
import glossary
import report_scan

app_module = pytest.importorskip('app')

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 64
WEBP = b'RIFF\x24\x00\x00\x00WEBP' + b'\x00' * 64


class Upload:
    """Imita el archivo subido de Flask: nombre y lectura acotada."""

    def __init__(self, filename, data):
        self.filename = filename
        self._stream = io.BytesIO(data)

    def read(self, size=-1):
        return self._stream.read(size)


@pytest.fixture
def ai(monkeypatch):
    """Sustituye la IA: ai.result es lo que 'lee' de la foto."""
    state = type('S', (), {})()
    state.result = {'title': 'La Revolución Francesa', 'topics': ['Causas', 'Etapas'], 'readable': True}
    state.calls = []

    def fake(contents, schema, instruction, usage_sink):
        state.calls.append({'contents': contents, 'schema': schema, 'instruction': instruction})
        if usage_sink is not None:
            usage_sink.append(40)
        if isinstance(state.result, Exception):
            raise state.result
        return state.result

    monkeypatch.setattr(glossary, '_json_generate', fake)
    return state


# ── Validación de seguridad (la misma función que el glosario) ────────

@pytest.mark.parametrize('name,data', [('a.png', PNG), ('a.jpg', JPG), ('a.jpeg', JPG), ('a.webp', WEBP),
                                       ('FOTO.PNG', PNG)])
def test_acepta_fotos_validas(ai, name, data):
    assert report_scan.extract_assignment(Upload(name, data))['title'] == 'La Revolución Francesa'


@pytest.mark.parametrize('name', ['guia.pdf', 'guia.docx', 'guia.txt', 'script.py', 'virus.exe', 'sin_extension', 'x.png.exe'])
def test_solo_se_permiten_imagenes(ai, name):
    with pytest.raises(ValueError, match='PNG, JPG o WebP') as exc:
        report_scan.extract_assignment(Upload(name, PNG))
    assert 'PDF' not in str(exc.value) and 'Word' not in str(exc.value)
    assert ai.calls == []                       # ni siquiera llega a la IA


@pytest.mark.parametrize('name,data', [
    ('foto.png', b'#!/bin/sh\nrm -rf /\n'),          # script renombrado a imagen
    ('foto.jpg', PNG),                               # firma de otro formato
    ('foto.png', JPG),
    ('foto.webp', b'RIFF\x00\x00\x00\x00WAVE' + b'\x00' * 32),   # RIFF pero no WebP
    ('foto.png', b'MZ\x90\x00' + b'\x00' * 64),      # ejecutable de Windows
    ('foto.jpg', b'<html><script>alert(1)</script>'),
])
def test_rechaza_archivos_disfrazados_por_su_firma_binaria(ai, name, data):
    with pytest.raises(ValueError, match='no coincide'):
        report_scan.extract_assignment(Upload(name, data))
    assert ai.calls == []


def test_rechaza_archivo_vacio(ai):
    with pytest.raises(ValueError, match='contenido'):
        report_scan.extract_assignment(Upload('a.png', b''))


def test_rechaza_archivo_demasiado_grande_sin_leerlo_completo(ai):
    big = Upload('a.png', PNG + b'\x00' * (glossary.MAX_UPLOAD_BYTES + 10))
    with pytest.raises(ValueError, match='pesar como máximo'):
        report_scan.extract_assignment(big)
    assert big._stream.tell() <= glossary.MAX_UPLOAD_BYTES + 1        # lectura acotada


def test_el_nombre_del_archivo_nunca_toca_el_disco(ai, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report_scan.extract_assignment(Upload('../../../etc/passwd.png', PNG))
    assert os.listdir(tmp_path) == []


def test_la_imagen_se_envia_como_parte_binaria_con_su_tipo_real(ai):
    report_scan.extract_assignment(Upload('a.jpg', JPG))
    part = ai.calls[0]['contents'][0]
    assert part.inline_data.mime_type == 'image/jpeg'


def test_la_instruccion_trata_la_foto_como_datos_no_como_ordenes(ai):
    report_scan.extract_assignment(Upload('a.png', PNG))
    instruction = ai.calls[0]['instruction']
    assert 'no instrucciones' in instruction and 'ignora cualquier orden' in instruction


def test_el_validador_del_glosario_conserva_su_mensaje_por_defecto():
    with pytest.raises(ValueError, match='PDF, Word'):
        glossary.secure_read_upload(Upload('a.exe', b'x'), {'.pdf'}, 1024)


# ── Saneado de la respuesta de la IA ──────────────────────────────────

def test_limpia_marcas_markdown_numeracion_y_espacios(ai):
    ai.result = {'title': '  **La   Revolución\tFrancesa**  ', 'readable': True,
                 'topics': ['1. Causas', '- Etapas', '* **Consecuencias**', '  ']}
    assert report_scan.extract_assignment(Upload('a.png', PNG)) == {
        'title': 'La Revolución Francesa', 'topics': ['Causas', 'Etapas', 'Consecuencias']}


def test_quita_caracteres_de_control_y_de_direccion(ai):
    ai.result = {'title': 'Tema\x00 de‮ prueba​ larga', 'readable': True, 'topics': ['A\x07B uno']}
    result = report_scan.extract_assignment(Upload('a.png', PNG))
    assert result['title'] == 'Tema de prueba larga' and result['topics'] == ['A B uno']


def test_acota_el_largo_del_titulo_y_de_los_temas(ai):
    ai.result = {'title': 'T' * 900, 'readable': True, 'topics': ['x' * 900]}
    result = report_scan.extract_assignment(Upload('a.png', PNG))
    assert len(result['title']) == report_scan.TITLE_MAX_LEN and len(result['topics'][0]) == report_scan.TOPIC_MAX_LEN


def test_maximo_8_temas_sin_repetidos(ai):
    ai.result = {'title': 'Un tema válido', 'readable': True,
                 'topics': [f'Tema {i}' for i in range(12)] + ['tema 1', 'TEMA 2']}
    assert len(report_scan.extract_assignment(Upload('a.png', PNG))['topics']) == 8


def test_sin_temas_devuelve_lista_vacia(ai):
    ai.result = {'title': 'Un tema válido', 'readable': True, 'topics': []}
    assert report_scan.extract_assignment(Upload('a.png', PNG))['topics'] == []


@pytest.mark.parametrize('result', [
    {'title': 'Algo', 'topics': [], 'readable': False},
    {'title': 'Algo largo', 'topics': [], 'readable': None},
    {'title': '', 'topics': [], 'readable': True},
    {'title': 'abc', 'topics': [], 'readable': True},          # menos de 5 caracteres
    {'title': '***', 'topics': [], 'readable': True},
    {'title': 12345, 'topics': [], 'readable': True},
    ['no', 'es', 'un', 'objeto'],
    None,
])
def test_consigna_ilegible_o_sin_tema_da_un_mensaje_claro(ai, result):
    ai.result = result
    with pytest.raises(ValueError):
        report_scan.extract_assignment(Upload('a.png', PNG))


# ── Ruta /report/scan ─────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    app_module.app.config['TESTING'] = True
    with app_module.app.app_context():
        uid = db.create_user('u@x.com', 'U')
        c = app_module.app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = uid
        yield c


def scan(client, name='consigna.png', data=PNG, field='photo', throttle=False):
    if not throttle:                    # el límite de frecuencia se prueba aparte
        with client.session_transaction() as sess:
            sess.pop('scan_at', None)
    payload = {} if data is None else {field: (io.BytesIO(data), name)}
    return client.post('/report/scan', data=payload, content_type='multipart/form-data')


def test_ruta_devuelve_titulo_y_temas(client, ai):
    response = scan(client)
    assert response.status_code == 200
    assert response.get_json() == {'title': 'La Revolución Francesa', 'topics': ['Causas', 'Etapas']}


def test_ruta_requiere_sesion(ai):
    anonymous = app_module.app.test_client()
    response = anonymous.post('/report/scan', data={'photo': (io.BytesIO(PNG), 'a.png')},
                              content_type='multipart/form-data')
    assert response.status_code == 302 and ai.calls == []


def test_ruta_sin_acceso_a_la_ia_responde_403_sin_gastar_tokens(client, ai):
    db.set_settings({'free_ai_enabled': '0'})
    response = scan(client)
    assert response.status_code == 403 and ai.calls == []
    assert 'EduLab AI' in response.get_json()['error']


def test_ruta_sin_archivo(client, ai):
    assert scan(client, data=None).status_code == 400
    assert scan(client, field='otro_campo').status_code == 400
    assert ai.calls == []


def test_ruta_rechaza_archivo_no_imagen_y_disfrazado(client, ai):
    assert scan(client, 'guia.pdf', b'%PDF-1.4 ...').status_code == 400
    response = scan(client, 'foto.png', b'#!/bin/sh\necho hack\n')
    assert response.status_code == 400 and 'no coincide' in response.get_json()['error']
    assert ai.calls == []


def test_ruta_errores_de_la_ia_son_502_con_mensaje(client, ai):
    import IA
    ai.result = IA.GenerationError('quota', 'La IA alcanzó su límite de uso.')
    response = scan(client)
    assert response.status_code == 502 and 'límite de uso' in response.get_json()['error']


def test_ruta_consigna_ilegible_es_400(client, ai):
    ai.result = {'title': '', 'topics': [], 'readable': False}
    response = scan(client)
    assert response.status_code == 400 and 'foto más clara' in response.get_json()['error']


def test_ruta_limita_la_frecuencia(client, ai):
    assert scan(client, throttle=True).status_code == 200
    assert scan(client, throttle=True).status_code == 429 and len(ai.calls) == 1


def test_ruta_archivo_enorme_responde_413_en_json(client, ai):
    app_module.app.config['MAX_CONTENT_LENGTH'] = 1024
    try:
        response = scan(client, data=PNG + b'\x00' * 5000)
    finally:
        app_module.app.config['MAX_CONTENT_LENGTH'] = None
    assert response.status_code == 413 and 'error' in response.get_json()


def test_los_tokens_de_la_lectura_se_suman_al_siguiente_informe(client, ai):
    scan(client)
    with client.session_transaction() as sess:
        assert sess['scan_extraction_tokens'] == 40


def test_aun_con_error_se_cuentan_los_tokens_gastados(client, ai):
    import IA

    def fail_after_spending(contents, schema, instruction, usage_sink):
        usage_sink.append(25)
        raise IA.GenerationError('quota', 'sin cuota')
    glossary._json_generate = fail_after_spending            # el fixture `ai` ya lo restaura
    scan(client)
    with client.session_transaction() as sess:
        assert sess['scan_extraction_tokens'] == 25


# ── Formulario ────────────────────────────────────────────────────────

def test_el_formulario_incluye_la_subida_de_foto_solo_imagenes(client):
    html = client.get('/form').get_data(as_text=True)
    assert 'id="scan-block"' in html and 'data-scan-url="/report/scan"' in html
    assert 'id="f-scan-photo"' in html and 'accept=".png,.jpg,.jpeg,.webp' in html
    assert '.pdf' not in html.split('id="f-scan-photo"')[1].split('>')[0]


def test_el_formulario_deshabilita_la_foto_sin_acceso_a_la_ia(client):
    db.set_settings({'free_ai_enabled': '0'})
    html = client.get('/form').get_data(as_text=True)
    tag = html.split('id="f-scan-photo"')[1].split('>')[0]
    assert 'disabled' in tag
