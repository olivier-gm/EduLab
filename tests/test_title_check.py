# -*- coding: utf-8 -*-
"""Validación del título con JEV (Choice de TypeSafe) y respaldo a Gemini."""
from types import SimpleNamespace

import pytest
import requests

import IA
import title_check
from title_check import TitleVerdict


def jev_payload(choice, valid=None, gibberish=0.0, not_a_topic=0.0):
    if valid is None:
        valid = 1.0 if choice == 'valid' else 0.0
    return {'answers': {'title_validity': {
        'type': 'choice', 'choice': choice, 'confidence': 1.0,
        'probabilities': {'valid': valid, 'gibberish': gibberish, 'not_a_topic': not_a_topic}}}}


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = str(body)

    def json(self):
        return self._body


@pytest.fixture
def jev(monkeypatch):
    """Clave presente y requests.post controlado: jev.responses es una lista de
    respuestas o excepciones que se van entregando en orden."""
    monkeypatch.setenv('JEV', 'clave-de-prueba')
    state = SimpleNamespace(calls=[], responses=[FakeResponse(200, jev_payload('valid'))])

    def post(url, headers=None, json=None, timeout=None):
        state.calls.append({'url': url, 'headers': headers, 'json': json})
        item = state.responses.pop(0) if len(state.responses) > 1 else state.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(title_check.requests, 'post', post)
    return state


@pytest.fixture
def gemini(monkeypatch):
    """Sustituye el validador de Gemini para saber si se usó el respaldo."""
    state = SimpleNamespace(calls=0, result=True)

    def fake(title, usage_sink=None):
        state.calls += 1
        if isinstance(state.result, Exception):
            raise state.result
        return state.result

    monkeypatch.setattr(title_check.IA, 'check_title', fake)
    return state


def test_titulo_valido(jev, gemini):
    verdict = title_check.check_title('La célula')
    assert verdict.valid and verdict.provider == 'jev' and gemini.calls == 0


def test_la_peticion_es_una_pregunta_choice_con_la_clave_en_el_header(jev, gemini):
    title_check.check_title('La célula')
    call = jev.calls[0]
    assert call['headers'] == {'Authorization': 'Bearer clave-de-prueba'}
    assert call['json']['state'] == 'La célula'
    question = call['json']['questions']['title_validity']
    assert question['type'] == 'choice' and set(question['criteria']) == {'valid', 'gibberish', 'not_a_topic'}
    assert 'clave-de-prueba' not in str(call['json'])


def test_texto_sin_sentido_se_rechaza_con_su_razon(jev, gemini):
    jev.responses = [FakeResponse(200, jev_payload('gibberish', valid=0.0, gibberish=0.99, not_a_topic=0.01))]
    verdict = title_check.check_title('asdkjh qwe')
    assert not verdict.valid and verdict.reason == 'gibberish'
    assert 'letras al azar' in verdict.message('asdkjh qwe')


def test_saludo_se_rechaza_como_no_tema(jev, gemini):
    jev.responses = [FakeResponse(200, jev_payload('not_a_topic', not_a_topic=1.0))]
    verdict = title_check.check_title('hola')
    assert not verdict.valid and verdict.reason == 'not_a_topic'
    assert 'saludo' in verdict.message('hola')


@pytest.mark.parametrize('p_valid,expected', [(0.51, True), (0.5, True), (0.49, False), (0.0, False)])
def test_umbral_de_probabilidad(jev, gemini, p_valid, expected):
    jev.responses = [FakeResponse(200, jev_payload('valid', valid=p_valid, gibberish=1 - p_valid))]
    assert title_check.check_title('x').valid is expected


# ── Respaldo a Gemini ─────────────────────────────────────────────────

def test_sin_clave_usa_gemini(monkeypatch, gemini):
    monkeypatch.delenv('JEV', raising=False)
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    verdict = title_check.check_title('La célula')
    assert verdict.provider == 'gemini' and verdict.valid and gemini.calls == 1


@pytest.mark.parametrize('failure', [
    requests.Timeout('lento'),
    requests.ConnectionError('sin red'),
    FakeResponse(503, {'error': 'x'}),
    FakeResponse(429, {'error': 'cuota'}),
    FakeResponse(401, {'error': 'clave'}),
])
def test_si_jev_falla_se_usa_gemini(jev, gemini, failure):
    jev.responses = [failure]
    verdict = title_check.check_title('La célula')
    assert verdict.provider == 'gemini' and verdict.valid and gemini.calls == 1


def test_reintenta_una_vez_ante_un_error_temporal(jev, gemini):
    jev.responses = [requests.Timeout('lento'), FakeResponse(200, jev_payload('valid'))]
    verdict = title_check.check_title('La célula')
    assert verdict.provider == 'jev' and len(jev.calls) == 2 and gemini.calls == 0


def test_errores_4xx_no_se_reintentan(jev, gemini):
    jev.responses = [FakeResponse(401, {'error': 'clave'})]
    title_check.check_title('La célula')
    assert len(jev.calls) == 1


def test_respuesta_rara_de_jev_cae_a_gemini(jev, gemini):
    jev.responses = [FakeResponse(200, {'answers': {}})]
    assert title_check.check_title('La célula').provider == 'gemini'


def test_el_respaldo_tambien_puede_rechazar(jev, gemini):
    jev.responses = [requests.ConnectionError('sin red')]
    gemini.result = False
    verdict = title_check.check_title('asdf')
    assert not verdict.valid and verdict.provider == 'gemini'
    assert 'no parece un tema' in verdict.message('asdf')


def test_si_fallan_las_dos_vias_se_lanza_error(jev, gemini):
    jev.responses = [requests.ConnectionError('sin red')]
    gemini.result = IA.GenerationError('quota', 'La IA alcanzó su límite.')
    with pytest.raises(IA.GenerationError):
        title_check.check_title('La célula')


@pytest.mark.parametrize('invalid_key', [None, 'title', 'term_99'])
def test_glossary_batches_individual_verdicts_and_accepts_medical_context(monkeypatch, invalid_key):
    terms = ['Pene', 'Vulva', 'Necrosis', 'Hemorragia', 'ITS'] + [f'Concepto {i}' for i in range(95)]
    if invalid_key == 'term_99':
        terms[-1] = 'asdfghjkl'
    calls = []
    monkeypatch.setenv('JEV', 'test-key')
    def post(url, headers, json, timeout):
        calls.append(json)
        assert len(json['questions']) <= 50
        if 'term_0' in json['questions']:
            assert json['state']['items']['term_0'] == 'Pene'
        if 'term_99' in json['questions']:
            assert '`items.term_99`' in json['questions']['term_99']['instructions']
        if 'title' in json['questions']:
            assert 'Anatomía genital' in json['questions']['title']['instructions']
        return FakeResponse(body={'answers': {key: jev_payload(
            'gibberish' if key == invalid_key else 'valid')['answers']['title_validity']
            for key in json['questions']}})
    monkeypatch.setattr(title_check.requests, 'post', post)
    if invalid_key:
        with pytest.raises(ValueError, match='título' if invalid_key == 'title' else 'asdfghjkl'):
            title_check.check_glossary('Anatomía clínica', terms)
    else:
        title_check.check_glossary('Anatomía clínica', terms)
    assert len(calls) == 3
    assert set().union(*(set(call['questions']) for call in calls)) == {'title'} | {f'term_{i}' for i in range(100)}


@pytest.mark.parametrize('failure', [FakeResponse(429), FakeResponse(body={'answers': {}})])
def test_glossary_fallback_checks_every_item_and_rejects_incomplete_results(jev, monkeypatch, failure):
    import glossary
    jev.responses = [failure]
    result = {'title': 'valid', 'term_0': 'valid', 'term_1': 'gibberish'}
    calls = []
    def generate(contents, schema, instruction, usage):
        calls.append(schema)
        assert schema['required'] == ['title', 'term_0', 'term_1']
        return result
    monkeypatch.setattr(glossary, '_json_generate', generate)
    with pytest.raises(ValueError, match='asdfghjkl'):
        title_check.check_glossary('Anatomía clínica', ['Pene', 'asdfghjkl'])
    assert len(calls) == 1
    result.pop('term_1')
    with pytest.raises(IA.GenerationError, match='lista completa'):
        title_check.check_glossary('Anatomía clínica', ['Pene', 'asdfghjkl'])


def test_glossary_without_jev_accepts_clinical_terms_using_ai(monkeypatch):
    import glossary
    monkeypatch.delenv('JEV', raising=False)
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    monkeypatch.setattr(glossary, '_json_generate', lambda *a: {'term_0': 'valid', 'term_1': 'valid'})
    title_check.check_glossary('Anatomía clínica', ['Vulva', 'Necrosis'], include_title=False)


def test_glossary_fallback_validates_all_300_terms_in_small_schemas(monkeypatch):
    import glossary
    monkeypatch.setattr(title_check, 'jev_available', lambda: False)
    calls = []
    def generate(contents, schema, instruction, usage):
        calls.append(schema['required'])
        assert len(schema['properties']) <= 50
        return {key: 'valid' for key in schema['required']}
    monkeypatch.setattr(glossary, '_json_generate', generate)
    title_check.check_glossary('Anatomía humana', [f'Término {i}' for i in range(300)], include_title=False)
    assert len(calls) == 6
    assert set().union(*map(set, calls)) == {f'term_{i}' for i in range(300)}
