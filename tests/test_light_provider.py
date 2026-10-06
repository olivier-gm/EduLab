# -*- coding: utf-8 -*-
"""Proveedor del modelo ligero independiente del principal, IDs de OpenRouter de cualquier
proveedor (con :floor) y esquema estricto para los modelos de OpenAI."""
import pytest
from google.genai import types

import ai_provider
import app as app_module
import db
import IA

app_module  # asegura que .env/conftest se aplican igual que en el resto de pruebas


@pytest.fixture
def calls(monkeypatch):
    """Registra (proveedor, modelo) de cada llamada; calls.fail = proveedores que fallan con 503."""
    monkeypatch.setenv('GEMINI_API_KEY', 'g-key')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'o-key')
    state = type('S', (), {})()
    state.log, state.fail = [], set()

    class Boom(Exception):
        code = 503

    def fake_call(provider, model, key, config, contents, values):
        state.log.append((provider, model))
        if provider in state.fail:
            raise Boom('caído')
        return 'ok'
    monkeypatch.setattr(ai_provider, '_call', fake_call)

    def use(**settings):
        values = dict(db.SETTING_DEFAULTS, **settings)
        monkeypatch.setattr(ai_provider, 'settings', lambda: values)
    state.use = use
    return state


def ask(light):
    return ai_provider.generate_content(model='gemini-3.5-flash-lite', config=types.GenerateContentConfig(),
                                        contents=['x'], light=light)


def test_principal_openrouter_y_ligero_en_gemini(calls):
    calls.use(ai_provider='openrouter', openrouter_model='openai/gpt-6-luna:floor', light_provider='gemini',
              gemini_light_model='gemini-3.5-flash-lite', openrouter_light_model='openai/gpt-6-luna:floor')
    ask(light=False)
    ask(light=True)
    assert calls.log == [('openrouter', 'openai/gpt-6-luna:floor'), ('gemini', 'gemini-3.5-flash-lite')]


def test_si_el_ligero_de_gemini_falla_cae_al_ligero_de_openrouter(calls):
    calls.use(ai_provider='openrouter', fallback_enabled='1', light_provider='gemini',
              gemini_light_model='gemini-3.5-flash-lite', openrouter_light_model='openai/gpt-6-luna:floor',
              openrouter_model='openai/gpt-6-luna')
    calls.fail = {'gemini'}
    assert ask(light=True) == 'ok'
    assert calls.log == [('gemini', 'gemini-3.5-flash-lite'), ('openrouter', 'openai/gpt-6-luna:floor')]


def test_si_falla_el_principal_openrouter_cae_al_gemini_original(calls):
    calls.use(ai_provider='openrouter', fallback_enabled='1', openrouter_model='openai/gpt-6-luna:floor',
              gemini_model='gemini-3.8-flash')
    calls.fail = {'openrouter'}
    assert ask(light=False) == 'ok'
    assert calls.log == [('openrouter', 'openai/gpt-6-luna:floor'), ('gemini', 'gemini-3.8-flash')]


def test_sin_fallback_el_error_del_ligero_se_propaga(calls):
    calls.use(ai_provider='openrouter', light_provider='gemini', gemini_light_model='gemini-3.5-flash-lite')
    calls.fail = {'gemini'}
    with pytest.raises(Exception):
        ask(light=True)
    assert calls.log == [('gemini', 'gemini-3.5-flash-lite')]


def test_light_provider_same_usa_el_proveedor_activo(calls):
    calls.use(ai_provider='openrouter', openrouter_model='openai/gpt-6-luna', openrouter_light_model='openai/gpt-6-luna:floor')
    ask(light=True)
    assert calls.log == [('openrouter', 'openai/gpt-6-luna:floor')]


# ── Admin ────────────────────────────────────────────────────────────

@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path / 'instance'))
    app_module.app.config['TESTING'] = True
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'o')
    with app_module.app.app_context():
        uid = db.create_user('adm@x.com', 'Adm')
        db.get_db().execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
        db.get_db().commit()
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
            sess['ai_csrf_token'] = 'tok'
        yield client


def save(admin, **fields):
    form = {'csrf_token': 'tok', 'ai_provider': 'openrouter', 'gemini_model': 'gemini-3.5-flash-lite',
            'openrouter_model': 'openai/gpt-6-luna:floor'}
    form.update(fields)
    admin.post('/admin/ai-settings', data=form)
    db._settings_cache.clear()
    return db.get_settings()


@pytest.mark.parametrize('model', ['openai/gpt-6-luna', 'openai/gpt-6-luna:floor', 'google/gemini-3.8-flash',
                                   'anthropic/claude-sonnet-5-5:nitro', 'meta-llama/llama-4-maverick'])
def test_admin_acepta_ids_de_openrouter_de_cualquier_proveedor(admin, model):
    assert save(admin, openrouter_model=model)['openrouter_model'] == model


@pytest.mark.parametrize('model', ['gpt-6-luna', 'openai/gpt-6-luna:online', 'openai/gpt 6', 'openai/gpt-6-luna:',
                                   'openai/../x', '/gpt', 'openai/gpt-6-luna:floor:floor', 'x' * 200])
def test_admin_rechaza_ids_invalidos(admin, model):
    assert save(admin, openrouter_model=model)['openrouter_model'] == db.SETTING_DEFAULTS['openrouter_model']


def test_admin_guarda_el_proveedor_del_ligero_y_su_modelo(admin):
    values = save(admin, light_provider='gemini', gemini_light_model='gemini-3.5-flash-lite',
                  openrouter_light_model='openai/gpt-6-luna:floor', fallback_enabled='on')
    assert values['light_provider'] == 'gemini' and values['openrouter_light_model'] == 'openai/gpt-6-luna:floor'
    assert save(admin, light_provider='otro')['light_provider'] == 'gemini'            # inválido: no cambia


def test_admin_exige_la_clave_del_proveedor_del_ligero(admin, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY')
    assert save(admin, light_provider='gemini')['light_provider'] == 'same'


# ── Esquema estricto para OpenAI ─────────────────────────────────────

def test_el_esquema_se_vuelve_estricto_sin_tocar_el_original():
    schema = {'type': 'object', 'properties': {
        'title': {'type': 'string'},
        'items': {'type': 'array', 'items': {'type': 'object', 'properties': {'a': {'type': 'string'}, 'b': {'type': 'boolean'}},
                                             'required': ['a']}}},
        'required': ['title']}
    strict = ai_provider._strict_schema(schema)
    assert strict['additionalProperties'] is False and strict['required'] == ['title', 'items']
    inner = strict['properties']['items']['items']
    assert inner['additionalProperties'] is False and inner['required'] == ['a', 'b']
    assert 'additionalProperties' not in schema and schema['required'] == ['title']


# ── Esfuerzo de razonamiento (OpenRouter) ────────────────────────────

def _body(monkeypatch, effort, max_tokens=20000):
    sent = []

    def post(url, headers, json, timeout):
        sent.append(json)
        return type('R', (), {'status_code': 200, 'json': lambda self: {
            'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': {}}})()
    monkeypatch.setattr(ai_provider.requests, 'post', post)
    ai_provider._openrouter('openai/gpt-6-luna', 'k', types.GenerateContentConfig(max_output_tokens=max_tokens), ['hola'], effort)
    return sent[0]


@pytest.mark.parametrize('effort,extra', [('low', 2000), ('medium', 6000), ('high', 16000), ('xhigh', 32000)])
def test_el_razonamiento_se_envia_con_margen_de_tokens(monkeypatch, effort, extra):
    body = _body(monkeypatch, effort)
    assert body['reasoning'] == {'effort': effort} and body['max_tokens'] == 20000 + extra


@pytest.mark.parametrize('effort', [None, '', 'max', 'xxx'])
def test_sin_esfuerzo_valido_no_se_envia_razonamiento(monkeypatch, effort):
    body = _body(monkeypatch, effort)
    assert 'reasoning' not in body and body['max_tokens'] == 20000


def test_principal_usa_el_esfuerzo_del_admin_y_el_ligero_usa_low(calls, monkeypatch):
    seen = []
    monkeypatch.setattr(ai_provider, '_call', lambda provider, model, key, config, contents, values:
                        seen.append((provider, values.get('_reasoning_effort'))) or 'ok')
    calls.use(ai_provider='openrouter', openrouter_reasoning='xhigh', light_provider='openrouter')
    ask(light=False)
    ask(light=True)
    assert seen == [('openrouter', 'xhigh'), ('openrouter', 'low')]


def test_admin_guarda_el_razonamiento_y_rechaza_valores_raros(admin):
    assert save(admin, openrouter_reasoning='xhigh')['openrouter_reasoning'] == 'xhigh'
    assert save(admin, openrouter_reasoning='max')['openrouter_reasoning'] == 'xhigh'    # inválido: no cambia
