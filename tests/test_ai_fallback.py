# -*- coding: utf-8 -*-
"""Fallback entre proveedores de IA (Gemini directo <-> OpenRouter) e
interruptor de búsqueda web por proveedor."""
import hashlib
import logging
from types import SimpleNamespace as NS

import pytest
from google.genai import types

import IA
import ai_provider
import app as app_module
import db
import glossary


class ApiError(Exception):
    """Imita un error de la API (lleva el código HTTP en .code)."""

    def __init__(self, code):
        super().__init__(f'HTTP {code}')
        self.code = code


GEMINI_OK = NS(text='respuesta de gemini', candidates=[NS(finish_reason='STOP')], usage_metadata=None)


@pytest.fixture
def engines(monkeypatch):
    """Los dos proveedores con clave y respuestas controladas por cada prueba.

    engines.gemini: lista de resultados o errores que devuelve Gemini directo.
    engines.openrouter: lista de status HTTP (200 = respuesta correcta)."""
    monkeypatch.setenv('GEMINI_API_KEY', 'g-key')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'o-key')
    monkeypatch.setattr(IA, '_search_blocked_until', 0)
    monkeypatch.setattr(IA, 'SEARCH_ENABLED', True)      # el .env puede traer GEMINI_GOOGLE_SEARCH=false
    monkeypatch.setattr(IA, 'CACHE_ENABLED', False)
    monkeypatch.setattr(IA.time, 'sleep', lambda s: None)
    state = NS(gemini=[GEMINI_OK], openrouter=[200], gemini_calls=[], openrouter_calls=[], token=None)

    def gemini(**kwargs):
        state.gemini_calls.append(kwargs)
        item = state.gemini.pop(0) if len(state.gemini) > 1 else state.gemini[0]
        if isinstance(item, Exception):
            raise item
        return item

    def post(url, headers, json, timeout):
        state.openrouter_calls.append(json)
        status = state.openrouter.pop(0) if len(state.openrouter) > 1 else state.openrouter[0]
        if status == 200:
            message = {'content': 'respuesta de openrouter'}
            return NS(status_code=200, json=lambda: {'choices': [{'message': message, 'finish_reason': 'stop'}],
                                                    'usage': {}})
        return NS(status_code=status, json=lambda: {'error': {'message': 'x'}})

    monkeypatch.setattr(IA, 'client', NS(models=NS(generate_content=gemini)))
    monkeypatch.setattr(ai_provider.requests, 'post', post)

    def use(provider='gemini', **overrides):
        if state.token is not None:
            ai_provider.request_settings.reset(state.token)
        state.token = ai_provider.request_settings.set(
            {**db.SETTING_DEFAULTS, 'ai_provider': provider, **overrides})

    state.use = use
    yield state
    if state.token is not None:
        ai_provider.request_settings.reset(state.token)


def ask(**kwargs):
    return ai_provider.generate_content(model='gemini-x', config=types.GenerateContentConfig(),
                                        contents=['Hola'], **kwargs)


def text_of(response):
    return IA._extract_text(response)


# ── Fallback entre proveedores ────────────────────────────────────────

def test_fallback_switches_provider_on_failure(engines):
    engines.use('openrouter', fallback_enabled='1')
    engines.openrouter = [500]
    assert text_of(ask()) == 'respuesta de gemini'
    assert len(engines.openrouter_calls) == 1 and len(engines.gemini_calls) == 1


def test_fallback_works_from_gemini_to_openrouter(engines):
    engines.use('gemini', fallback_enabled='1')
    engines.gemini = [ApiError(429)]
    assert text_of(ask()) == 'respuesta de openrouter'


def test_fallback_disabled_does_not_retry(engines):
    engines.use('openrouter', fallback_enabled='0')
    engines.openrouter = [500]
    with pytest.raises(Exception) as exc:
        ask()
    assert IA.classify_error(exc.value).code == 'unavailable'
    assert engines.gemini_calls == []


@pytest.mark.parametrize('status', [403, 401, 400])
def test_fallback_auth_and_bad_request_errors_not_retried(engines, status):
    engines.use('openrouter', fallback_enabled='1')
    engines.openrouter = [status]
    with pytest.raises(Exception):
        ask()
    assert engines.gemini_calls == []


def test_fallback_blocked_content_is_not_retried(engines):
    engines.use('gemini', fallback_enabled='1')
    engines.gemini = [IA.GenerationError('blocked', 'bloqueado')]
    with pytest.raises(IA.GenerationError) as exc:
        ask()
    assert exc.value.code == 'blocked' and engines.openrouter_calls == []


def test_fallback_without_key_of_the_other_provider_raises_original_error(engines, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY')
    engines.use('gemini', fallback_enabled='1')
    engines.gemini = [ApiError(503)]
    with pytest.raises(ApiError):
        ask()
    assert engines.openrouter_calls == []


def test_if_the_fallback_also_fails_the_original_error_is_raised(engines):
    engines.use('openrouter', fallback_enabled='1')
    engines.openrouter = [500]
    engines.gemini = [ApiError(429)]
    with pytest.raises(Exception) as exc:
        ask()
    assert 'OpenRouter' in str(exc.value)                  # el error del proveedor principal
    assert len(engines.gemini_calls) == 1


def test_fallback_is_logged(engines, caplog):
    engines.use('gemini', fallback_enabled='1')
    engines.gemini = [ApiError(503)]
    with caplog.at_level(logging.INFO, logger='ai_provider'):
        ask()
    messages = ' | '.join(r.getMessage() for r in caplog.records)
    assert 'gemini falló' in messages and 'Fallback automático a openrouter' in messages
    assert 'respondió correctamente' in messages


def test_failed_fallback_is_logged(engines, caplog):
    engines.use('gemini', fallback_enabled='1')
    engines.gemini = [ApiError(503)]
    engines.openrouter = [500]
    with caplog.at_level(logging.INFO, logger='ai_provider'):
        with pytest.raises(ApiError):
            ask()
    assert any('también falló' in r.getMessage() for r in caplog.records)


def test_fallback_to_openrouter_gets_the_full_request_when_gemini_used_its_cache(engines):
    """Con el caché de Gemini la petición no lleva instrucciones: el otro proveedor
    debe recibir la versión completa."""
    engines.use('gemini', fallback_enabled='1')
    engines.gemini = [ApiError(429)]
    cached = types.GenerateContentConfig(cached_content='caches/1')
    full = types.GenerateContentConfig(system_instruction='Instrucción completa')
    ai_provider.generate_content(
        model='gemini-x', config=cached, contents=['solo la pregunta'],
        fallback_config=full, fallback_contents=['ejemplo', 'solo la pregunta'])
    sent = engines.openrouter_calls[0]['messages']
    assert sent[0] == {'role': 'system', 'content': 'Instrucción completa'}


def test_a_cached_prompt_passes_the_full_request_to_the_fallback(engines):
    engines.use('gemini', fallback_enabled='1')
    engines.gemini = [ApiError(503)]
    prompt = IA._FewShotPrompt('t', 'gemini-x', 'Sé breve', [('pregunta ejemplo', 'respuesta ejemplo')])
    prompt._cache_name = 'caches/1'
    prompt._cache_attempted = True
    prompt._cache_identity = (ai_provider.configuration('gemini-x')[0], 'gemini-x',
                              hashlib.sha256(b'g-key').digest())
    prompt.generate('Tema: X', temperature=0.1, max_output_tokens=50)
    texts = [m['content'] if isinstance(m['content'], str) else m['content'][0]['text']
             for m in engines.openrouter_calls[0]['messages']]
    assert texts[0].startswith('Sé breve') and 'pregunta ejemplo' in texts and 'respuesta ejemplo' in texts


# ── Interruptor de búsqueda por proveedor ─────────────────────────────

def search_config():
    return types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])


def test_search_is_enabled_by_default_for_both_providers_and_fallback_is_off():
    assert db.SETTING_DEFAULTS['gemini_search_enabled'] == '1'
    assert db.SETTING_DEFAULTS['openrouter_search_enabled'] == '1'
    assert db.SETTING_DEFAULTS['fallback_enabled'] == '0'


def test_search_toggle_strips_the_tool_for_that_provider(engines, caplog):
    engines.use('gemini', gemini_search_enabled='0')
    with caplog.at_level(logging.INFO, logger='ai_provider'):
        ai_provider.generate_content(model='gemini-x', config=search_config(), contents=['Hola'])
    assert engines.gemini_calls[0]['config'].tools is None
    assert any('desactivada para gemini' in r.getMessage() for r in caplog.records)


def test_search_toggle_keeps_the_tool_when_enabled(engines):
    engines.use('gemini')
    ai_provider.generate_content(model='gemini-x', config=search_config(), contents=['Hola'])
    assert engines.gemini_calls[0]['config'].tools


def test_each_provider_has_its_own_search_toggle_also_in_fallback(engines):
    engines.use('gemini', fallback_enabled='1', openrouter_search_enabled='0')
    engines.gemini = [ApiError(503)]
    ai_provider.generate_content(model='gemini-x', config=search_config(), contents=['Hola'])
    assert engines.gemini_calls[0]['config'].tools                 # el principal sí busca
    assert 'tools' not in engines.openrouter_calls[0]              # el fallback no


def test_essay_does_not_request_search_when_the_active_provider_has_it_off(engines):
    prompt = IA._FewShotPrompt('essay', 'gemini-x', 'Sé breve', [])
    engines.use('gemini', gemini_search_enabled='0')
    prompt.generate('Tema: X', temperature=0.1, max_output_tokens=50, use_search=True)
    assert all(not call['config'].tools for call in engines.gemini_calls)
    engines.gemini_calls.clear()
    engines.use('gemini', gemini_search_enabled='1')
    prompt.generate('Tema: X', temperature=0.1, max_output_tokens=50, use_search=True)
    assert engines.gemini_calls[0]['config'].tools


def test_failed_search_falls_back_to_no_search_and_is_logged(engines, caplog):
    prompt = IA._FewShotPrompt('essay', 'gemini-x', 'Sé breve', [])
    engines.use('gemini')
    engines.gemini = [ApiError(429), GEMINI_OK]
    warnings = []
    with caplog.at_level(logging.WARNING):
        result = prompt.generate('Tema: X', temperature=0.1, max_output_tokens=50,
                                 use_search=True, warnings=warnings)
    assert result == 'respuesta de gemini'
    assert warnings == [IA.SEARCH_UNAVAILABLE_WARNING]
    assert not engines.gemini_calls[1]['config'].tools
    assert any('Búsqueda en tiempo real falló' in r.getMessage() for r in caplog.records)


def test_bibliography_respects_the_toggle_and_logs_a_search_failure(engines, caplog):
    engines.use('gemini', gemini_search_enabled='0')
    sources, _, reason = glossary._source_context('Biología', 'Células', [])
    assert sources == [] and 'desactivada' in reason and engines.gemini_calls == []

    engines.use('gemini')
    engines.gemini = [ApiError(500)]
    with caplog.at_level(logging.WARNING):
        sources, _, reason = glossary._source_context('Biología', 'Células', [])
    assert sources == [] and 'no disponible' in reason
    assert any('búsqueda de la bibliografía falló' in r.getMessage() for r in caplog.records)


# ── Panel admin ───────────────────────────────────────────────────────

@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    app_module.app.config['TESTING'] = True
    with app_module.app.app_context():
        uid = db.create_user('adm@x.com', 'Adm')
        db.get_db().execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
        db.get_db().commit()
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
            sess['ai_csrf_token'] = 'tok'
        yield client


def save(client, **fields):
    form = {'csrf_token': 'tok', 'ai_provider': 'gemini', 'gemini_model': 'gemini-3.5-flash-lite',
            'openrouter_model': 'google/gemini-3.8-flash'}
    form.update(fields)
    return client.post('/admin/ai-settings', data=form, follow_redirects=True).get_data(as_text=True)


def stored(key):
    db._settings_cache.clear()
    return db.get_settings()[key]


def test_admin_saves_the_fallback_and_search_toggles(admin, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'o')
    html = save(admin, fallback_enabled='on', gemini_search_enabled='on')
    assert 'Configuración de IA guardada' in html
    assert stored('fallback_enabled') == '1'
    assert stored('gemini_search_enabled') == '1' and stored('openrouter_search_enabled') == '0'


def test_admin_unchecked_boxes_turn_the_options_off(admin, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    save(admin)
    assert stored('fallback_enabled') == '0'
    assert stored('gemini_search_enabled') == '0' and stored('openrouter_search_enabled') == '0'


def test_admin_cannot_enable_the_fallback_without_the_other_providers_key(admin, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    html = save(admin, fallback_enabled='on')
    assert 'clave API de los dos proveedores' in html
    assert stored('fallback_enabled') == '0'


def test_admin_panel_shows_the_new_options(admin):
    html = admin.get('/admin/').get_data(as_text=True)
    assert 'name="fallback_enabled"' in html
    assert 'name="gemini_search_enabled"' in html and 'name="openrouter_search_enabled"' in html
