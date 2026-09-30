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
