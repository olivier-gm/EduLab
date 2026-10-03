# -*- coding: utf-8 -*-
"""Mayúsculas y tildes del título y subtítulos: propuesta del modelo ligero, control de
código (solo mayúsculas/tildes), revisión de JEV y respaldo de primera letra."""
import pytest

import ai_provider
import db
import text_format as tf
import title_check
from form_processor import FormProcessor


@pytest.fixture
def model(monkeypatch):
    """Simula al modelo: model.answer = {clave: (texto, confident)}."""
    state = type('S', (), {})()
    state.answer, state.calls = {}, []

    def propose(texts, usage_sink):
        state.calls.append(dict(texts))
        if isinstance(state.answer, Exception):
            raise state.answer
        return {k: v[0] for k, v in state.answer.items() if k in texts and v[1]}
    monkeypatch.setattr(tf, '_propose', propose)
    monkeypatch.setattr(title_check, 'jev_available', lambda: False)
    return state


def run(title='la vida de jose antonio paez', subtitles=('infancia',)):
    return tf.format_texts(title, list(subtitles))


# ── Control de código ────────────────────────────────────────────────

@pytest.mark.parametrize('original,proposed,ok', [
    ('la vida de jose antonio paez', 'La vida de José Antonio Páez', True),
    ('la historia de la ucv', 'La historia de la UCV', True),
    ('la historia de la ucv', 'La historia de la UCV en Venezuela', False),     # palabra agregada
    ('la historia de la ucv', 'La historia UCV', False),                         # palabra quitada
    ('causas de la guerra', 'La guerra de las causas', False),                   # reordenada
    ('año nuevo', 'Ano nuevo', False),                                           # ñ -> n
    ('ano nuevo', 'Año nuevo', False),                                           # n -> ñ
    ('pinguino', 'Pingüino', True),
    ('el cráneo humano', 'El craneo humano', True),                              # quitar tilde
    ('a, b', 'A b', False),                                                      # puntuación
    ('hola', '', False), ('hola', None, False), ('hola', 123, False),
    ('ciclo de krebs', 'Ciclo de Krebs', True),
    ('ciclo de krebs', 'Ciclo de Krebz', False),                                 # cambia una letra
    ('café', 'Cafè', False), ('façade', 'Facade', False),                        # otros diacríticos
    ('Straße', 'STRASSE', False), ('Straße', 'STRAẞE', True),                    # no expande letras
    ('an\u0303o', 'Año', True), ('cra\u0301neo', 'CRÁNEO', True),                  # Unicode equivalente
])
def test_control_de_codigo(original, proposed, ok):
    assert tf.only_case_and_accents_changed(original, proposed) is ok


# ── Flujo completo ───────────────────────────────────────────────────

def test_aplica_las_correcciones_del_modelo(model):
    model.answer = {'title': ('La vida de José Antonio Páez', True), 's0': ('Infancia', True)}
    assert run() == {'title': 'La vida de José Antonio Páez', 'title_corrected': True, 'subtitles': ['Infancia']}


def test_sigla(model):
    model.answer = {'title': ('La historia de la UCV', True)}
    assert run('la historia de la ucv', [])['title'] == 'La historia de la UCV'


def test_si_el_modelo_no_esta_seguro_ese_texto_queda_con_la_primera_letra(model):
    model.answer = {'title': ('La vida de José Antonio Páez', True), 's0': ('Infancia de Xyz', False)}
    result = run(subtitles=['infancia de xyz'])
    assert result['title'] == 'La vida de José Antonio Páez' and result['subtitles'] == ['Infancia de xyz']


def test_propuesta_que_cambia_palabras_se_descarta_solo_ese_texto(model):
    model.answer = {'title': ('La vida de José Antonio Páez y su época', True), 's0': ('Infancia', True)}
    result = run()
    assert result['title'] == 'La vida de jose antonio paez' and result['title_corrected'] is False
    assert result['subtitles'] == ['Infancia']


@pytest.mark.parametrize('failure', [RuntimeError('caído'), ValueError('json')])
def test_si_el_modelo_falla_se_usa_el_respaldo(model, failure):
    model.answer = failure
    assert run() == {'title': 'La vida de jose antonio paez', 'title_corrected': False, 'subtitles': ['Infancia']}


def test_no_manda_campos_vacios_ni_enormes(model):
    model.answer = {}
    tf.format_texts('mi tema', ['', 'x' * 500])
    assert model.calls[0] == {'title': 'mi tema'}


# ── JEV ──────────────────────────────────────────────────────────────

def jev(monkeypatch, probabilities=None, fail=False):
    monkeypatch.setattr(title_check, 'jev_available', lambda: True)
    sent = []

    def call(state, questions=None):
        sent.append((state, questions))
        if fail is True or fail == state['aspect']:
            raise RuntimeError('JEV caído')
        scores = {k: probabilities.get((state['aspect'], k), probabilities.get(k, 0)) for k in questions}
        return {'answers': {k: {'choice': 'correct' if scores[k] >= .5 else 'incorrect',
                                'probabilities': {'correct': scores[k]}} for k in questions}}
    monkeypatch.setattr(title_check, '_call_jev', call)
    return sent


def test_jev_aprueba_y_solo_revisa_lo_que_cambio(model, monkeypatch):
    model.answer = {'title': ('La vida de José Antonio Páez', True), 's0': ('Infancia', True)}
    sent = jev(monkeypatch, {'title': .9})
    assert run()['title'] == 'La vida de José Antonio Páez'
    assert len(sent) == 2 and {state['aspect'] for state, _ in sent} == {'case', 'accents'}
    assert all(list(questions) == ['title'] for _, questions in sent)  # 'Infancia' ya era igual al respaldo
    state = next(state for state, _ in sent if state['aspect'] == 'case')
    assert state['items']['title']['proposed'] == 'La vida de Jose Antonio Paez'
    state = next(state for state, _ in sent if state['aspect'] == 'accents')
    assert state['items']['title']['proposed'] == 'La vida de josé antonio páez'


def test_jev_rechaza_una_correccion_y_queda_el_respaldo(model, monkeypatch):
    model.answer = {'title': ('La Vida De José Antonio Páez', True)}
    jev(monkeypatch, {'title': .1})
    result = run(subtitles=[])
    assert result['title'] == 'La vida de jose antonio paez' and result['title_corrected'] is False


def test_si_jev_falla_se_confia_en_el_control_de_codigo(model, monkeypatch):
    model.answer = {'title': ('La vida de José Antonio Páez', True)}
    jev(monkeypatch, fail=True)
    assert run(subtitles=[])['title'] == 'La vida de José Antonio Páez'


@pytest.mark.parametrize('case,accents,expected,corrected', [
    (.9, .1, 'La vida de Jose Antonio Paez', True),
    (.1, .9, 'La vida de josé antonio páez', True),
    (.1, .1, 'La vida de jose antonio paez', False),
    (.9, .9, 'La vida de José Antonio Páez', True),
])
def test_las_dos_capas_se_conservan_independientemente(model, monkeypatch, case, accents, expected, corrected):
    model.answer = {'title': ('La vida de José Antonio Páez', True)}
    jev(monkeypatch, {('case', 'title'): case, ('accents', 'title'): accents})
    result = run(subtitles=[])
    assert result['title'] == expected and result['title_corrected'] is corrected


def test_rechazo_de_tilde_no_borra_siglas_ni_tilde_que_ya_estaba(model, monkeypatch):
    model.answer = {'title': ('La anatomía del CRANÉO en la UCV', True)}
    jev(monkeypatch, {('case', 'title'): .9, ('accents', 'title'): .1})
    assert run('la anatomía del craneo en la ucv', [])['title'] == 'La anatomía del CRANEO en la UCV'


def test_rechazo_de_title_case_no_borra_tildes_aprobadas(model, monkeypatch):
    model.answer = {'title': ('La Vida De José Antonio Páez', True)}
    jev(monkeypatch, {('case', 'title'): .1, ('accents', 'title'): .9})
    assert run(subtitles=[])['title'] == 'La vida de josé antonio páez'


@pytest.mark.parametrize('aspect,expected', [
    ('case', 'La vida de Jose Antonio Paez'), ('accents', 'La vida de josé antonio páez'),
])
def test_fallo_de_una_peticion_no_oculta_rechazo_de_la_otra(model, monkeypatch, aspect, expected):
    model.answer = {'title': ('La vida de José Antonio Páez', True)}
    jev(monkeypatch, {'title': .1}, fail=aspect)
    assert run(subtitles=[])['title'] == expected


@pytest.mark.parametrize('bad', [None, {}, {'choice': 'correct', 'probabilities': {'correct': 'nan'}},
                                  {'choice': 'correct', 'probabilities': {'correct': 2}},
                                  {'choice': 'incorrect', 'probabilities': {'correct': .9}}])
def test_respuesta_invalida_no_aprueba_ni_descarta_otros_campos(model, monkeypatch, bad):
    model.answer = {'title': ('Cráneo', True), 's0': ('Hábitat', True)}
    monkeypatch.setattr(title_check, 'jev_available', lambda: True)
    monkeypatch.setattr(title_check, '_call_jev', lambda *a: {'answers': {
        'title': bad, 's0': {'choice': 'correct', 'probabilities': {'correct': .9}}}})
    assert run('craneo', ['habitat']) == {'title': 'Craneo', 'title_corrected': False, 'subtitles': ['Hábitat']}


def test_unicode_no_cambia_el_numero_de_letras(model):
    model.answer = {'title': ('STRAẞE', True)}
    assert run('Straße', [])['title'] == 'STRAẞE'


def test_cada_capa_solo_revisa_sus_cambios_y_tiene_contexto(model, monkeypatch):
    model.answer = {'title': ('La historia de la UCV', True), 's0': ('Fundación', True)}
    sent = jev(monkeypatch, {'title': .9, 's0': .9})
    run('la historia de la ucv', ['fundacion'])
    assert len(sent) == 2
    for state, questions in sent:
        assert state['context']['title'] == 'la historia de la ucv'
        key = 'title' if state['aspect'] == 'case' else 's0'
        assert list(questions) == [key]
        expected = [{'original': 'ucv', 'proposed': 'UCV'}] if key == 'title' else [
            {'original': 'Fundacion', 'proposed': 'Fundación', 'accent_positions': [8], 'accented_vowels': ['ó']}]
        assert state['items'][key]['changes'] == expected


@pytest.mark.parametrize('first,second,expected', [
    (.4, .95, 'La historia de la UCV'),   # recupera un rechazo dudoso
    (.6, .1, 'La historia de la ucv'),    # detecta una aprobación dudosa
    (.4, .6, 'La historia de la ucv'),    # una segunda duda no basta
])
def test_una_decision_dudosa_se_revisa_sin_bajar_el_umbral(model, monkeypatch, first, second, expected):
    model.answer = {'title': ('La historia de la UCV', True), 's0': ('El ciclo de Krebs', True)}
    monkeypatch.setattr(title_check, 'jev_available', lambda: True)
    sent = []

    def call(state, questions):
        sent.append(state)
        score = first if len(sent) == 1 else second
        return {'answers': {key: {'choice': 'correct' if p >= .5 else 'incorrect', 'probabilities': {'correct': p}}
                            for key, p in ((key, score if key == 'title' else .95) for key in questions)}}
    monkeypatch.setattr(title_check, '_call_jev', call)
    result = run('la historia de la ucv', ['el ciclo de krebs'])
    assert result['title'] == expected and result['subtitles'] == ['El ciclo de Krebs']
    assert len(sent) == 2 and list(sent[1]['items']) == ['title']
    assert sent[1]['context'] == {'title': 'la historia de la ucv'}


def test_si_la_revision_de_tildes_falla_se_conservan_las_mayusculas(model, monkeypatch):
    model.answer = {'title': ('La vida de José Antonio Páez', True)}
    monkeypatch.setattr(title_check, 'jev_available', lambda: True)
    calls = []

    def call(state, questions):
        calls.append(state['aspect'])
        if state['aspect'] == 'accents' and calls.count('accents') == 2:
            raise RuntimeError('sin red')
        p = .9 if state['aspect'] == 'case' else .4
        return {'answers': {'title': {'choice': 'correct' if p >= .5 else 'incorrect', 'probabilities': {'correct': p}}}}
    monkeypatch.setattr(title_check, '_call_jev', call)
    assert run(subtitles=[])['title'] == 'La vida de Jose Antonio Paez'
    assert calls.count('accents') == 2


def test_perder_jev_durante_una_revision_no_aprueba_la_duda(model, monkeypatch):
    model.answer = {'title': ('La historia de la UCV', True)}
    sent = []
    monkeypatch.setattr(title_check, 'jev_available', lambda: not sent)

    def call(state, questions):
        sent.append(state)
        return {'answers': {'title': {'choice': 'incorrect', 'probabilities': {'correct': .4}}}}
    monkeypatch.setattr(title_check, '_call_jev', call)
    assert run('la historia de la ucv', [])['title'] == 'La historia de la ucv'
    assert len(sent) == 1


def test_jev_no_ve_lo_que_el_control_de_codigo_ya_descarto(model, monkeypatch):
    model.answer = {'title': ('Otra cosa distinta', True)}
    sent = jev(monkeypatch, {})
    run(subtitles=[])
    assert sent == []


# ── Integración con el formulario y el admin ─────────────────────────

def test_el_procesador_aplica_titulo_y_subtitulos(model):
    processor = FormProcessor({'title': 'la historia de la ucv', 'subtitle_1': 'que es...', 'subtitle_3': 'fundacion'}, 'uni')
    model.answer = {'title': ('La historia de la UCV', True), 's0': ('Que es', True), 's1': ('Fundación', True)}
    processor.apply_formatted_texts(tf.format_texts(processor.title, processor.subtitles))
    processor.process()
    _, head_title = processor.generate_replacements()
    assert head_title == 'La historia de la UCV'          # sin pasar por capitalizar_frases ('Ucv')
    assert processor.subtitles == ['Que es', 'Fundación'] and processor.original_title == 'la historia de la ucv'


def test_sin_correccion_el_titulo_conserva_el_formato_de_siempre(model):
    model.answer = RuntimeError('x')
    processor = FormProcessor({'title': 'la vida de jose'}, 'uni')
    processor.apply_formatted_texts(tf.format_texts(processor.title, processor.subtitles))
    processor.process()
    assert processor.generate_replacements()[1] == 'La Vida de Jose'


def test_el_modelo_ligero_se_usa_si_esta_definido_y_si_no_el_principal():
    base = dict(db.SETTING_DEFAULTS, gemini_model='gemini-pro', openrouter_model='google/gemini-pro')
    assert ai_provider._light_values(base)['gemini_model'] == 'gemini-pro'
    light = dict(base, gemini_light_model='gemini-lite', openrouter_light_model='google/gemini-lite')
    values = ai_provider._light_values(light)
    assert values['gemini_model'] == 'gemini-lite' and values['openrouter_model'] == 'google/gemini-lite'
    assert light['gemini_model'] == 'gemini-pro'          # no modifica los ajustes originales
