import pytest

from form_processor import FormProcessor


@pytest.mark.parametrize('city,expected', [
    (' \tCaracas \t', 'CARACAS, 12 DE OCTUBRE DE 2026'),
    ('  San Juan de los Morros  ', 'SAN JUAN DE LOS MORROS, 12 DE OCTUBRE DE 2026'),
    ('   ', '12 DE OCTUBRE DE 2026'),
])
def test_city_is_trimmed_before_joining_cover_date(city, expected):
    processor = FormProcessor({'city': city, 'date': '12/10/2026'}, 'uni')
    processor.process()
    replacements, _ = processor.generate_replacements()
    assert replacements['[city]'] + replacements['[date]'] == expected


def test_city_trim_also_applies_to_school_covers():
    processor = FormProcessor({'city': '  Caracas  ', 'u': 'Liceo', 'estado': 'Miranda'}, 'bach')
    processor.process()
    replacements, _ = processor.generate_replacements()
    assert replacements['[city]'] == 'CARACAS - EDO. '


def test_trim_cover_data_before_adding_labels_and_formatting():
    processor = FormProcessor({
        'u': '  Universidad Central de Venezuela  ', 'area': '  Medicina  ',
        'carrera': '  Enfermería  ', 'title': '  La célula  ', 'teacher': '  José Pérez  ',
        'asignatura': '  Biología  ', 'seccion': '  A  ', 'date': '  12/10/2026  ',
        'input1': '  Ana María  ', 'id1': '  12345678  ', 'input2': '   ', 'id2': '   ',
        'gblock-template-canvas-integrantes': '2',
    }, 'uni')
    processor.process()
    replacements, _ = processor.generate_replacements()
    for key, expected in {
        '[u]': 'UNIVERSIDAD CENTRAL DE VENEZUELA', '[area]': 'MEDICINA',
        '[carrera]': 'ENFERMERÍA', '[title]': 'LA CÉLULA', '[teacher]': 'JOSÉ PÉREZ',
        '[asignatura]': 'BIOLOGÍA', '[seccion]': 'SECCION: "A"',
        '[date]': '12 DE OCTUBRE DE 2026', '[1]': 'ANA MARÍA', '[id1]': ' C.I- 12345678',
        '[2]': '', '[id2]': '', '[alumnos]': 'ALUMNO:',
    }.items():
        assert replacements[key] == expected


@pytest.mark.parametrize('route', ['uni', 'bach'])
def test_whitespace_only_optional_fields_do_not_create_labels(route):
    processor = FormProcessor({'teacher': '   ', 'seccion': '   ', 'input1': '   ', 'id1': '   ',
                               'gblock-template-canvas-integrantes': '1'}, route)
    processor.process()
    replacements, _ = processor.generate_replacements()
    for key in ('[teacher]', '[docente]', '[seccion]', '[alumnos]', '[1]', '[id1]'):
        assert replacements[key] == ''


def test_trim_does_not_mutate_input_or_change_manual_paragraphs():
    original = {'title': '  La célula  ', 'body': '  Primer párrafo.\n\nSegundo párrafo.  ',
                'introduccion': '\n Introducción.\n', 'conclusion': ' Conclusión.\n'}
    processor = FormProcessor(original, 'uni')
    assert original['title'] == '  La célula  '
    for key in ('body', 'introduccion', 'conclusion'):
        assert getattr(processor, key) == original[key]
