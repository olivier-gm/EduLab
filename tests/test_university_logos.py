from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from docx.shared import Cm
from PIL import Image

from algorythms import Document_process


@pytest.mark.parametrize('extension', ['png', 'webp', 'svg'])
def test_logo_is_embedded_in_word_with_transparency(tmp_path, extension):
    path = tmp_path / f'universidad_de_prueba.{extension}'
    if extension == 'svg':
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
            '<rect x="10" y="10" width="20" height="20" fill="#00cccc"/>'
            '</svg>', encoding='utf-8')
    else:
        picture = Image.new('RGBA', (40, 40))
        picture.paste((0, 204, 204, 255), (10, 10, 30, 30))
        picture.save(path, lossless=True)
    document = Document()
    document.add_paragraph('Portada')
    assert Document_process.insert_logo(document, 'Universidad de Prúeba', str(tmp_path))
    assert len(document.inline_shapes) == 1
    assert document.inline_shapes[0].width == Cm(3)
    output = BytesIO()
    document.save(output)
    with ZipFile(output) as archive:
        media = [name for name in archive.namelist() if name.startswith('word/media/')]
        assert len(media) == 1
        with Image.open(BytesIO(archive.read(media[0]))) as embedded:
            assert embedded.format == 'PNG'
            assert embedded.convert('RGBA').getpixel((0, 0))[3] == 0
    assert document.paragraphs[1].text == 'Portada'


def test_invalid_svg_does_not_change_cover(tmp_path):
    (tmp_path / 'universidad.svg').write_text('invalid svg', encoding='utf-8')
    document = Document()
    document.add_paragraph('Portada')
    assert not Document_process.insert_logo(document, 'Universidad', str(tmp_path))
    assert [paragraph.text for paragraph in document.paragraphs] == ['Portada']
    assert not document.inline_shapes
