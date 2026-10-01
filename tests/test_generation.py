# -*- coding: utf-8 -*-
"""Generación del informe: errores con motivo claro en vez de portada sola o
vuelta silenciosa al inicio."""
import os
from types import SimpleNamespace

import pytest

import db
from title_check import TitleVerdict

IA = pytest.importorskip('IA')
app_module = pytest.importorskip('app')


# ── Utilidades ────────────────────────────────────────────────────────

class ApiError(Exception):
    """Imita google.genai.errors.APIError (lleva el código HTTP en .code)."""

    def __init__(self, code, message=''):
        super().__init__(f'{code} {message}')
        self.code = code


def response(text='', finish='FinishReason.STOP', block=None):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=finish)],
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
        usage_metadata=None,
    )


@pytest.fixture
def fake_api(monkeypatch):
    """Reemplaza el cliente de Gemini. `handler(config)` decide qué devolver."""
    state = SimpleNamespace(calls=[], handler=lambda config: response('TRUE'))

    def generate_content(model, config, contents):
        state.calls.append(config)
        return state.handler(config)

    monkeypatch.setattr(IA, 'client', SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    monkeypatch.setattr(IA, 'CACHE_ENABLED', False)
    monkeypatch.setattr(IA, 'SEARCH_ENABLED', True)
    monkeypatch.setattr(IA, '_search_blocked_until', 0.0)
    monkeypatch.setattr(IA.time, 'sleep', lambda s: None)
    return state


# ── IA: clasificación de errores ──────────────────────────────────────

@pytest.mark.parametrize('code,expected', [
    (429, 'quota'), (401, 'auth'), (403, 'auth'),
    (503, 'unavailable'), (500, 'unavailable'), (400, 'bad_request'), (418, 'unknown'),
])
def test_classify_error_por_codigo_http(code, expected):
    err = IA.classify_error(ApiError(code))
    assert err.code == expected and err.user_message


def test_classify_error_timeout_de_red():
    assert IA.classify_error(TimeoutError('boom')).code == 'unavailable'


def test_extract_text_explica_por_que_esta_vacio():
    with pytest.raises(IA.GenerationError) as e:
        IA._extract_text(response('', block='SAFETY'))
    assert e.value.code == 'blocked'
    with pytest.raises(IA.GenerationError) as e:
        IA._extract_text(response('', finish='FinishReason.MAX_TOKENS'))
    assert e.value.code == 'truncated'
    with pytest.raises(IA.GenerationError) as e:
        IA._extract_text(response(''))
    assert e.value.code == 'empty'
    assert IA._extract_text(response('hola')) == 'hola'


# ── IA: validación de título ──────────────────────────────────────────

@pytest.mark.parametrize('answer,expected', [
    ('TRUE', True), ('True.', True), ('  true\n', True),
    ('FALSE', False), ('False', False),
    ('no sé', True),   # formato inesperado: no es un rechazo
])
def test_check_title_interpreta_la_respuesta(fake_api, answer, expected):
    fake_api.handler = lambda config: response(answer)
    assert IA.check_title('Un titulo') is expected


def test_check_title_error_de_api_no_es_titulo_invalido(fake_api):
    def boom(config):
        raise ApiError(429, 'RESOURCE_EXHAUSTED')
    fake_api.handler = boom
    with pytest.raises(IA.GenerationError) as e:
        IA.check_title('Un titulo')
    assert e.value.code == 'quota'


def test_check_title_respuesta_vacia_se_reporta(fake_api):
    fake_api.handler = lambda config: response('')
    with pytest.raises(IA.GenerationError) as e:
        IA.check_title('Un titulo')
    assert e.value.code == 'empty'


def test_errores_permanentes_no_se_reintentan(fake_api):
    def boom(config):
        raise ApiError(400, 'bad')
    fake_api.handler = boom
    with pytest.raises(IA.GenerationError):
        IA.check_title('Un titulo')
    assert len(fake_api.calls) == 1


# ── IA: búsqueda en tiempo real con respaldo ──────────────────────────

def test_ensayo_usa_google_search_cuando_esta_disponible(fake_api):
    fake_api.handler = lambda config: response('Texto con datos actuales.')
    warnings = []
    assert IA.generate_essay_content('Bitcoin', [], warnings=warnings) == 'Texto con datos actuales.'
    assert fake_api.calls[0].tools, 'la primera llamada debe llevar google_search'
    assert warnings == []


def test_si_la_busqueda_falla_se_genera_sin_ella_y_se_avisa(fake_api):
    def handler(config):
        if config.tools:
            raise ApiError(429, 'RESOURCE_EXHAUSTED')
        return response('Texto sin búsqueda.')
    fake_api.handler = handler
    warnings = []
    assert IA.generate_essay_content('Bitcoin', [], warnings=warnings) == 'Texto sin búsqueda.'
    assert warnings == [IA.SEARCH_UNAVAILABLE_WARNING]
    assert IA._search_blocked_until > 0


def test_tras_error_de_cuota_no_se_reintenta_la_busqueda(fake_api):
    def handler(config):
        if config.tools:
            raise ApiError(429, 'RESOURCE_EXHAUSTED')
        return response('ok')
    fake_api.handler = handler
    IA.generate_essay_content('Uno', [], warnings=[])
    n = len(fake_api.calls)
    warnings = []
    IA.generate_essay_content('Dos', [], warnings=warnings)
    assert len(fake_api.calls) == n + 1          # solo la llamada sin búsqueda
    assert not fake_api.calls[-1].tools
    assert warnings == [IA.SEARCH_UNAVAILABLE_WARNING]


def test_ensayo_vacio_lanza_error_con_motivo(fake_api):
    fake_api.handler = lambda config: response('')
    with pytest.raises(IA.GenerationError) as e:
        IA.generate_essay_content('Tema', [])
    assert e.value.code == 'empty'


def test_busqueda_se_puede_desactivar(fake_api, monkeypatch):
    monkeypatch.setattr(IA, 'SEARCH_ENABLED', False)
    fake_api.handler = lambda config: response('sin búsqueda')
    IA.generate_essay_content('Tema', [])
    assert not fake_api.calls[0].tools


# ── App: mensajes al usuario ──────────────────────────────────────────

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


@pytest.fixture
def fake_document(monkeypatch):
    """Evita LibreOffice: escribe un .docx (y .pdf) mínimos donde se pide."""
    created = []

    def fill(docx_output, *args, **kwargs):
        os.makedirs(os.path.dirname(docx_output), exist_ok=True)
        open(docx_output, 'wb').write(b'x')
        if not fill.skip_pdf:
            open(docx_output[:-5] + '.pdf', 'wb').write(b'x')
        created.append(docx_output)
    fill.skip_pdf = False

    monkeypatch.setattr(app_module.Document_process, 'fill_placeholders', staticmethod(fill))
    yield fill
    for path in created:
        for ext in ('.docx', '.pdf'):
            if os.path.exists(path[:-5] + ext):
                os.remove(path[:-5] + ext)


def post_form(client, **extra):
    data = {'title': 'La inteligencia artificial', 'global-mode': 'ia',
            'incluir_introduccion': '1', 'incluir_conclusion': '1'}
    data.update(extra)
    return client.post('/process_form', data=data, follow_redirects=True).get_data(as_text=True)


def test_parallel_sections_keep_selected_provider(client, monkeypatch, fake_document):
    import ai_provider
    import threading
    db.set_settings({'ai_provider': 'openrouter', 'openrouter_model': 'google/gemini-3.8-flash'})
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-thread-key')
    monkeypatch.setattr(IA, 'CACHE_ENABLED', False)
    monkeypatch.setattr(app_module, 'check_title', lambda *a, **k: TitleVerdict(True))
    monkeypatch.setattr(app_module, 'generate_essay_content', lambda *a, **k: 'Desarrollo de prueba.')
    calls = []
    def post(url, headers, json, timeout):
        calls.append((json['model'], threading.get_ident(), headers['Authorization']))
        return SimpleNamespace(status_code=200, json=lambda: {'choices': [
            {'message': {'content': 'Sección de prueba.'}}], 'usage': {'total_tokens': 10}})
    monkeypatch.setattr(ai_provider.requests, 'post', post)
    assert 'Descargar Word' in post_form(client)
    assert len(calls) == 2 and all(model == 'google/gemini-3.8-flash' and thread != threading.get_ident()
        and auth == 'Bearer test-thread-key' for model, thread, auth in calls)


def test_titulo_corto_explica_el_motivo(client):
    html = post_form(client, title='ab')
    assert 'mínimo 5 caracteres' in html


def test_titulo_solo_numeros_se_rechaza(client):
    assert 'no solo números o símbolos' in post_form(client, title='123456')


def test_titulo_rechazado_por_la_ia_dice_por_que(client, monkeypatch):
    monkeypatch.setattr(app_module, 'check_title',
                        lambda *a, **k: TitleVerdict(False, 'gibberish'))
    html = post_form(client, title='asdkjh qwe')
    assert 'no reconoce' in html and 'letras al azar' in html


def test_error_de_api_al_validar_no_se_culpa_al_titulo(client, monkeypatch):
    def boom(*a, **k):
        raise IA.GenerationError('quota', 'La IA alcanzó su límite de uso en este momento.')
    monkeypatch.setattr(app_module, 'check_title', boom)
    html = post_form(client)
    assert 'No se pudo validar el título' in html and 'límite de uso' in html
    assert 'no reconoce' not in html


def test_ensayo_fallido_no_entrega_portada_sola(client, monkeypatch, fake_document):
    monkeypatch.setattr(app_module, 'check_title', lambda *a, **k: TitleVerdict(True))

    def boom(*a, **k):
        raise IA.GenerationError('quota', 'La IA alcanzó su límite de uso en este momento.')
    monkeypatch.setattr(app_module, 'generate_essay_content', boom)
    html = post_form(client)
    assert 'No se pudo generar el desarrollo' in html
    with app_module.app.app_context():
        assert db.get_stats()['total_documents'] == 0   # no consumió un documento


def test_intro_fallida_entrega_el_documento_con_aviso(client, monkeypatch, fake_document):
    monkeypatch.setattr(app_module, 'check_title', lambda *a, **k: TitleVerdict(True))
    monkeypatch.setattr(app_module, 'generate_essay_content',
                        lambda *a, warnings=None, **k: warnings.append('sin búsqueda actualizada') or 'Cuerpo')

    def boom(*a, **k):
        raise IA.GenerationError('unavailable', 'Los servidores de la IA están saturados.')
    monkeypatch.setattr(app_module, 'generate_introduction', boom)
    monkeypatch.setattr(app_module, 'generate_conclusion', lambda *a, **k: 'Conclusión')

    html = post_form(client)
    assert 'No se incluyó la introducción' in html
    assert 'sin búsqueda actualizada' in html
    assert 'Descargar Word' in html


def test_manual_en_blanco_genera_portada_sola_avisando(client, fake_document):
    html = post_form(client, **{'global-mode': 'standard'})
    assert 'solo la portada' in html and 'Descargar Word' in html


def test_aviso_si_no_se_pudo_generar_el_pdf(client, fake_document):
    fake_document.skip_pdf = True
    html = post_form(client, **{'global-mode': 'standard', 'body': 'Algo de contenido'})
    assert 'No se pudo generar el PDF' in html


def test_error_armando_el_documento_no_es_un_500(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('libreoffice murió')
    monkeypatch.setattr(app_module.Document_process, 'fill_placeholders', staticmethod(boom))
    html = post_form(client, **{'global-mode': 'standard', 'body': 'x'})
    assert 'error armando el documento' in html


def test_descarga_sin_sesion_explica_y_no_manda_al_inicio(client):
    resp = client.get('/choose_file/algo', follow_redirects=True)
    assert 'ya no está disponible' in resp.get_data(as_text=True)


def test_archivo_expirado_se_avisa(client):
    with client.session_transaction() as sess:
        sess['file_generated'] = True
    html = client.get('/choose_file/no_existe_zz', follow_redirects=True).get_data(as_text=True)
    assert 'ya no existe' in html


@pytest.mark.parametrize('title,expected', [
    ('Qué es: el ¿amor? a/b', 'Qué es_ el ¿amor_ a_b'),
    ('///', 'documento'),
])
def test_safe_filename(title, expected):
    assert app_module.safe_filename(title) == expected
    assert len(app_module.safe_filename('x' * 300)) <= 80


def test_glossary_route_enforces_ai_and_ignores_intro_conclusion(client, monkeypatch, fake_document):
    captured = {}
    entries = [{'term': 'Átomo', 'definition': 'Unidad de materia.', 'reference': ''}]
    def generate(title, count, **kwargs):
        captured.update(count=count, **kwargs)
        kwargs['usage_sink'].append(25)
        return entries
    monkeypatch.setattr(app_module, 'generate_glossary', generate)
    def unexpected(*a, **k):
        raise AssertionError('Un glosario no debe generar secciones de un ensayo.')
    for name in ('generate_introduction', 'generate_conclusion', 'generate_essay_content', 'check_title'):
        monkeypatch.setattr(app_module, name, unexpected)
    html = post_form(client, document_kind='glossary', glossary_source='list', glossary_terms='Átomo')
    assert 'Descargar Word' in html and captured['count'] == 1 and not captured['bibliography']
    assert 'Glosario en orden alfabético' in html and 'Índice incluido' not in html
    assert db.get_db().execute('SELECT mode, tokens_used FROM documents').fetchone()['mode'] == 'ai'
    db.set_settings({'free_ai_enabled': '0'})
    resp = client.post('/process_form', data={'title': 'Biología', 'document_kind': 'glossary', 'global-mode': 'standard'})
    assert '/plans' in resp.location


def test_glossary_count_rejected_before_api(client, monkeypatch):
    monkeypatch.setattr(app_module, 'generate_glossary', lambda *a, **k: pytest.fail('No llamar a la IA'))
    html = post_form(client, document_kind='glossary', glossary_count='101')
    assert 'entre 1 y 100' in html


def test_manual_bibliography_is_optional(client, monkeypatch, fake_document):
    monkeypatch.setattr(app_module, 'generate_bibliography', lambda *a, **k: pytest.fail('Manual no usa IA'))
    assert 'Descargar Word' in post_form(client, **{'global-mode': 'standard', 'body': 'Texto.',
        'incluir_bibliografia': '1', 'bibliografia': 'Autor. Fuente.'})
    page = client.get('/form').get_data(as_text=True)
    assert 'id="f-incluir-bib" name="incluir_bibliografia" value="1">' in page
