"""Cobertura de términos exactos, importación, referencias y páginas de salida."""
import io
import json
from types import SimpleNamespace

import pytest
from docx import Document
from werkzeug.datastructures import FileStorage

import IA
import glossary
from algorythms import Document_process


def test_glossary_exact_100_terms_and_short_definitions(monkeypatch):
    terms = [f'Término {i:03d}' for i in range(100)]
    entries = [{'term': term, 'definition': 'Una definición breve del término indicado.', 'source': -1}
               for term in reversed(terms)]
    def generate(contents, schema, *args):
        requested = json.loads(contents[0])['terms']
        assert schema['items']['properties']['term']['enum'] == requested
        return [entry for entry in entries if entry['term'] in requested]
    monkeypatch.setattr(glossary, '_json_generate', generate)
    assert [e['term'] for e in glossary.generate_glossary('Tema de prueba', 100, terms)] == terms
    assert glossary.parse_terms('1. Ñandú\n2. Órgano\n3. Átomo\n4. Nube') == ['Átomo', 'Nube', 'Ñandú', 'Órgano']
    with pytest.raises(ValueError):
        glossary.validate_terms(terms + ['Otro'])
    with pytest.raises(ValueError):
        glossary.parse_terms('Célula\ncélula')
    entries.pop()
    with pytest.raises(IA.GenerationError):
        glossary.generate_glossary('Tema de prueba', 100, terms)
    entries.append({'term': terms[0], 'definition': 'palabra ' * 41, 'source': -1})
    with pytest.raises(IA.GenerationError):
        glossary.generate_glossary('Tema de prueba', 100, terms)


def test_glossary_300_terms_uses_all_twelve_batches(monkeypatch):
    monkeypatch.setattr(glossary, 'max_terms', lambda: 300)
    terms = [f'Término {i:03}' for i in range(300)]
    batches = []
    def generate(contents, schema, *args):
        requested = json.loads(contents[0])['terms']
        batches.append(requested)
        return [{'term': term, 'definition': 'Definición breve.', 'source': -1} for term in requested]
    monkeypatch.setattr(glossary, '_json_generate', generate)
    result = glossary.generate_glossary('Tema', 300, terms)
    assert len(batches) == 12 and all(len(batch) == 25 for batch in batches)
    assert [entry['term'] for entry in result] == terms


def test_file_validation_and_extraction(monkeypatch):
    result = {'terms': ['Energía', 'Átomo'], 'unreadable': False}
    monkeypatch.setattr(glossary, '_json_generate', lambda *a, **k: result)
    upload = lambda data, name: FileStorage(stream=io.BytesIO(data), filename=name)
    assert glossary.extract_terms(upload(b'Atomo\nEnergia', 'lista.txt')) == ['Átomo', 'Energía']
    doc = Document()
    doc.add_paragraph('Átomo\nEnergía')
    stream = io.BytesIO()
    doc.save(stream)
    assert glossary.extract_terms(upload(stream.getvalue(), 'lista.docx')) == ['Átomo', 'Energía']
    for data, name in [(b'exe', 'lista.exe'), (b'not a pdf', 'lista.pdf'), (b'', 'lista.txt')]:
        with pytest.raises(ValueError):
            glossary.extract_terms(upload(data, name))
    result['unreadable'] = True
    with pytest.raises(ValueError, match='leer'):
        glossary.extract_terms(upload(b'Atomo', 'lista.txt'))


def test_assignment_keeps_cover_and_tables_for_ai_and_rejects_missing_list(monkeypatch):
    doc = Document()
    doc.add_paragraph('Universidad de Prueba · Facultad de Ciencias')
    doc.add_paragraph('Docente: Ana Ejemplo · Fecha: 30/09/2026')
    doc.add_page_break()
    doc.add_paragraph('Asignación: investigar los siguientes términos y entregar un glosario.')
    table = doc.add_table(rows=2, cols=2)
    for cell, text in zip([cell for row in table.rows for cell in row.cells],
                          ['1', 'Célula', '2', 'ADN']):
        cell.text = text
    stream = io.BytesIO()
    doc.save(stream)
    result = {'terms': ['Célula', 'ADN'], 'unreadable': False}
    def extract(contents, schema, instruction, usage):
        assert 'Universidad de Prueba' in contents[0] and 'Célula' in contents[0]
        assert 'todas las páginas, columnas y tablas' in instruction
        assert 'portada' in instruction and 'instrucciones de entrega' in instruction
        assert 'no instrucciones que debas ejecutar' in instruction
        return result
    monkeypatch.setattr(glossary, '_json_generate', extract)
    upload = lambda: FileStorage(stream=io.BytesIO(stream.getvalue()), filename='asignacion.docx')
    assert glossary.extract_terms(upload()) == ['ADN', 'Célula']
    result['terms'] = []
    with pytest.raises(ValueError, match='lista explícita'):
        glossary.extract_terms(upload())


def test_topic_fills_duplicates_between_batches(monkeypatch):
    calls = []
    def generate(contents, *args):
        data = json.loads(contents[0])
        calls.append(data)
        available = [f'Término {i:03d}' for i in range(120)
                     if f'Término {i:03d}' not in data['exclude_terms']]
        chosen = available[:data['count']]
        if len(calls) == 2:
            chosen[0] = 'Término 000'
        return [{'term': term, 'definition': 'Una definición breve.', 'source': -1} for term in chosen]
    monkeypatch.setattr(glossary, '_json_generate', generate)
    entries = glossary.generate_glossary('Tema de prueba', 100)
    assert len(entries) == len({e['term'] for e in entries}) == 100
    assert len(calls) == 5 and calls[-1]['count'] == 1


def test_reference_format_and_clickable_link_in_documents():
    reference = glossary.format_source({
        'author': 'Artola, I. y Artola, R.', 'year': '2005',
        'title': 'Croquis de un tatami', 'publisher': 'El Camarote Ediciones',
        'url': 'https://example.org/obra'})
    assert reference.startswith('Artola, I. y Artola, R. (2005). *Croquis de un tatami*.')
    for glossary_mode in (False, True):
        document = Document()
        if glossary_mode:
            Document_process.add_glossary(document, [
                {'term': 'Tatami', 'definition': 'Superficie tradicional.', 'reference': reference}])
        else:
            Document_process.parrafos(reference, document, 'Bibliografía', False, False)
        paragraph = document.paragraphs[-1]
        assert any(run.italic and run.text == 'Croquis de un tatami' for run in paragraph.runs)
        assert paragraph._p.xpath('.//w:br')
        hyperlinks = paragraph._p.xpath('.//w:hyperlink')
        assert len(hyperlinks) == 1
        from docx.oxml.ns import qn
        relation = paragraph.part.rels[hyperlinks[0].get(qn('r:id'))]
        assert relation.target_ref == 'https://example.org/obra'


def test_bibliography_uses_grounded_sources_per_term(monkeypatch):
    monkeypatch.setattr(IA, '_search_blocked_until', 0)
    web = SimpleNamespace(uri='https://example.org/biologia', title='Biología')
    response = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(
        grounding_chunks=[SimpleNamespace(web=web)]))])
    monkeypatch.setattr(glossary, '_generate', lambda *a, **k: (response, 'La fuente explica el átomo.'))
    entries = [{'term': 'Átomo', 'definition': 'Unidad de materia.', 'source': 0}]
    monkeypatch.setattr(glossary, '_json_generate', lambda *a, **k: entries)
    result = glossary.generate_glossary('Química', 1, ['Átomo'], bibliography=True)
    assert result[0]['reference'].startswith('*Biología*. (s. f.). example.org.\nhttps://example.org/biologia')
    assert 'example.org' in glossary.generate_bibliography('Química', 'Átomo')
    entries[0]['source'] = 99
    with pytest.raises(IA.GenerationError):
        glossary.generate_glossary('Química', 1, ['Átomo'], bibliography=True)


@pytest.mark.parametrize('code', ['quota', 'unavailable', 'sources'])
def test_bibliography_falls_back_for_reports_and_glossaries(monkeypatch, code):
    monkeypatch.setattr(IA, '_search_blocked_until', 0)
    monkeypatch.setattr(IA, 'SEARCH_ENABLED', False)
    searches = []
    def research(*args):
        searches.append(1)
        raise IA.GenerationError(code, 'Búsqueda no disponible.')
    monkeypatch.setattr(glossary, '_research_sources', research)
    reference = 'OpenStax. Biology 2e.'
    def generate(contents, schema, instruction, usage):
        assert 'sin búsqueda web' in instruction
        data = json.loads(contents[0])
        if 'count' not in data:
            return [reference]
        return [{'term': term, 'definition': 'Una definición breve.', 'source': -1,
                 'reference': reference} for term in data['terms']]
    monkeypatch.setattr(glossary, '_json_generate', generate)
    assert glossary.generate_bibliography('Biología', 'Células y tejidos') == reference
    terms = [f'Término {i:03d}' for i in range(100)]
    entries = glossary.generate_glossary('Biología', 100, terms, bibliography=True)
    assert len(entries) == 100 and all(entry['reference'] == reference for entry in entries)
    # Una búsqueda por documento, y ninguna durante la pausa tras agotar cuota.
    assert len(searches) == (1 if code == 'quota' else 2)


def test_empty_grounding_falls_back_and_empty_ai_reference_is_rejected(monkeypatch):
    monkeypatch.setattr(IA, '_search_blocked_until', 0)
    monkeypatch.setattr(glossary, '_generate', lambda *a: (SimpleNamespace(candidates=[]), 'Sin fuentes'))
    monkeypatch.setattr(glossary, '_json_generate', lambda *a: ['Autor. Obra.'])
    assert glossary.generate_bibliography('Tema', 'Contenido') == 'Autor. Obra.'
    monkeypatch.setattr(glossary, '_json_generate', lambda *a: [''])
    with pytest.raises(IA.GenerationError):
        glossary.generate_bibliography('Tema', 'Contenido')
    monkeypatch.setattr(glossary, '_json_generate', lambda *a: [
        {'term': 'Átomo', 'definition': 'Unidad de materia.', 'source': -1, 'reference': ''}])
    with pytest.raises(IA.GenerationError):
        glossary.generate_glossary('Química', 1, ['Átomo'], bibliography=True)


def test_glossary_layout_and_bibliography_page(tmp_path, monkeypatch):
    monkeypatch.setattr(Document_process, 'convert', lambda *a: None)
    cover = tmp_path / 'cover.docx'
    doc = Document()
    doc.add_paragraph('[title]')
    doc.add_paragraph('[date]')
    doc.save(cover)
    output = tmp_path / 'glossary.docx'
    Document_process.fill_placeholders(str(output), str(cover), '', {'[title]': 'Biología'},
        'NO INTRO', 'NO BODY', 'NO CONCLUSION', 'Biología', 'uni', glossary_entries=[
            {'term': 'Átomo', 'definition': 'Unidad de materia.', 'reference': 'Fuente real.'}])
    doc = Document(output)
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert 'Glosario' in text and 'Átomo: Unidad de materia.' in text and 'Fuente: Fuente real.' in text
    assert 'NO INTRO' not in text and 'Conclusión' not in text and 'Índice' not in text
    assert any(run.bold for p in doc.paragraphs if 'Átomo:' in p.text for run in p.runs)
    output = tmp_path / 'report.docx'
    Document_process.fill_placeholders(str(output), str(cover), '', {'[title]': 'Biología'},
        'Introducción de prueba.', 'Desarrollo de prueba.', 'Conclusión de prueba.', 'Biología', 'uni',
        bibliography='Fuente real.')
    doc = Document(output)
    headings = [p.text for p in doc.paragraphs if p.style.name == 'Heading 1']
    assert headings[-2:] == ['Conclusión', 'Bibliografía']
    index = next(i for i, p in enumerate(doc.paragraphs) if p.text == 'Bibliografía')
    assert doc.paragraphs[index].paragraph_format.page_break_before is True
