"""Adaptación de búsqueda, archivos, JSON y errores de OpenRouter."""
import json
from types import SimpleNamespace as NS

import pytest
from google.genai import types

import IA
import ai_provider
import db
import glossary


def test_openrouter_search_json_files_and_tokens(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-openrouter-secret')
    monkeypatch.setattr(IA, '_search_blocked_until', 0)
    token = ai_provider.request_settings.set({**db.SETTING_DEFAULTS, 'ai_provider': 'openrouter'})
    calls = []
    def post(url, headers, json, timeout):
        assert url == 'https://openrouter.ai/api/v1/chat/completions'
        assert headers['Authorization'] == 'Bearer test-openrouter-secret'
        calls.append(json)
        if json.get('tools'):
            message = {'content': 'La fuente explica las células.', 'annotations': [
                {'type': 'url_citation', 'url_citation': {'url': 'https://example.org/celulas', 'title': 'Biología'}}]}
        else:
            message = {'content': '{"terms":["Célula"],"unreadable":false}'}
        return NS(status_code=200, json=lambda: {'choices': [{'message': message, 'finish_reason': 'stop'}],
                                               'usage': {'prompt_tokens': 20, 'completion_tokens': 10}})
    monkeypatch.setattr(ai_provider.requests, 'post', post)
    try:
        usage = []
        assert 'https://example.org/celulas' in glossary.generate_bibliography('Biología', 'Células', usage)
        assert calls[0]['model'] == 'google/gemini-3.8-flash'
        assert calls[0]['tools'][0]['parameters']['engine'] == 'native'
        assert calls[0]['max_tool_calls'] == 3 and usage == [30]
        schema = {'type': 'object', 'properties': {'terms': {'type': 'array', 'items': {'type': 'string'}}}}
        response = ai_provider.generate_content(model='unused', config=types.GenerateContentConfig(
            system_instruction='Lee los términos', response_json_schema=schema), contents=[
            types.Part.from_bytes(data=b'%PDF-test', mime_type='application/pdf'),
            types.Part.from_bytes(data=b'image', mime_type='image/png'), 'Extrae la lista'])
        parts = calls[-1]['messages'][1]['content']
        assert parts[0]['file']['file_data'].startswith('data:application/pdf;base64,')
        assert parts[1]['image_url']['url'].startswith('data:image/png;base64,')
        assert calls[-1]['plugins'][0]['pdf']['engine'] == 'native'
        # Se envía en modo estricto (lo exigen los modelos de OpenAI): sin propiedades extra y todas requeridas.
        assert calls[-1]['response_format']['json_schema']['schema'] == {**schema, 'additionalProperties': False, 'required': ['terms']}
        assert json.loads(IA._extract_text(response))['terms'] == ['Célula']
    finally:
        ai_provider.request_settings.reset(token)


def test_openrouter_search_failure_uses_ai_bibliography(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-secret')
    monkeypatch.setattr(IA, '_search_blocked_until', 0)
    token = ai_provider.request_settings.set({**db.SETTING_DEFAULTS, 'ai_provider': 'openrouter'})
    calls = []
    def post(url, headers, json, timeout):
        calls.append(json)
        if json.get('tools'):
            return NS(status_code=429, json=lambda: {'error': {'message': 'secret must not be logged'}})
        return NS(status_code=200, json=lambda: {'choices': [{'message': {'content': '{"items":["OpenStax. Biology 2e."]}'}}]})
    monkeypatch.setattr(ai_provider.requests, 'post', post)
    try:
        assert glossary.generate_bibliography('Biología', 'Células') == 'OpenStax. Biology 2e.'
        assert len(calls) == 2 and not calls[1].get('tools')
    finally:
        ai_provider.request_settings.reset(token)


def test_model_override_and_cache_do_not_cross_providers(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-google')
    calls = []
    monkeypatch.setattr(IA, 'client', NS(models=NS(generate_content=lambda **kwargs: calls.append(kwargs))))
    monkeypatch.setattr(IA, 'CACHE_ENABLED', False)
    token = ai_provider.request_settings.set({**db.SETTING_DEFAULTS, 'gemini_model': 'gemini-3.8-flash'})
    try:
        ai_provider.generate_content(model='old-model', config=types.GenerateContentConfig(), contents=['Hola'])
        assert calls[0]['model'] == 'gemini-3.8-flash'
        prompt = IA._FewShotPrompt('Test', 'old-model', 'Instruction', [])
        prompt._cache_name = 'old-cache'
        prompt._cache_attempted = True
        prompt._ensure_cache()
        assert prompt._cache_name is None
        ai_provider.request_settings.set({**db.SETTING_DEFAULTS, 'ai_provider': 'openrouter'})
        prompt._cache_name = 'google-cache'
        prompt._ensure_cache()
        assert prompt._cache_name is None
    finally:
        ai_provider.request_settings.reset(token)


@pytest.mark.parametrize('status,code', [(402, 'quota'), (404, 'bad_request'), (403, 'auth')])
def test_openrouter_errors_do_not_expose_provider_body(monkeypatch, status, code):
    monkeypatch.setattr(ai_provider.requests, 'post', lambda *a, **k: NS(status_code=status,
        json=lambda: {'error': {'message': 'secret-api-key'}}))
    with pytest.raises(Exception) as exc:
        ai_provider._openrouter('google/gemini-3.8-flash', 'key', types.GenerateContentConfig(), ['Hola'])
    assert 'secret-api-key' not in str(exc.value)
    assert IA.classify_error(exc.value).code == code


@pytest.mark.parametrize('model', ['openai/gpt-6-sol', 'openai/gpt-6-luna'])
def test_gpt6_omits_unsupported_sampling_parameters(monkeypatch, model):
    bodies = []

    def post(*args, **kwargs):
        body = kwargs['json']
        bodies.append(body)
        assert 'temperature' not in body and 'top_p' not in body
        return NS(status_code=200, json=lambda: {
            'choices': [{'message': {'content': 'Respuesta de prueba'}}]})

    monkeypatch.setattr(ai_provider.requests, 'post', post)
    result = ai_provider._openrouter(model, 'key', types.GenerateContentConfig(
        temperature=0.7, top_p=0.9, max_output_tokens=1000), ['Hola'], 'high')
    assert result.text == 'Respuesta de prueba'
    assert bodies[0]['reasoning'] == {'effort': 'high'}
    assert bodies[0]['provider']['require_parameters'] is True


def test_openrouter_wraps_root_array_schema_and_restores_array_response(monkeypatch):
    bodies = []

    def post(*args, **kwargs):
        bodies.append(kwargs['json'])
        return NS(status_code=200, json=lambda: {
            'choices': [{'message': {'content': '{"items":["A","B"]}'}}]})

    monkeypatch.setattr(ai_provider.requests, 'post', post)
    schema = {'type': 'array', 'items': {'type': 'string'}}
    response = ai_provider._openrouter('openai/gpt-6-luna', 'key',
        types.GenerateContentConfig(response_json_schema=schema), ['Lista'])
    submitted = bodies[0]['response_format']['json_schema']['schema']
    assert submitted['type'] == 'object' and submitted['required'] == ['items']
    assert submitted['properties']['items']['type'] == 'array'
    assert json.loads(response.text) == ['A', 'B']


def test_openrouter_404_explains_routing_without_claiming_model_missing(monkeypatch):
    monkeypatch.setattr(ai_provider.requests, 'post', lambda *a, **k: NS(
        status_code=404, json=lambda: {'error': {'message': 'No endpoints found'}}))
    with pytest.raises(IA.GenerationError) as exc:
        ai_provider._openrouter('openai/gpt-6-sol', 'key', types.GenerateContentConfig(), ['Hola'])
    assert 'proveedor disponible' in exc.value.user_message
    assert 'no existe' not in exc.value.user_message
