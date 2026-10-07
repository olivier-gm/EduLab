# -*- coding: utf-8 -*-
"""Rotación de logos de la landing sin trabas: miniaturas web (tools/fix_logos.py), la landing que
las usa y el brillo del papel animado con transform (no repinta la tarjeta en cada cuadro)."""
import importlib.util
import io
import os
import re

import pytest
from PIL import Image

import db
import landing
from app import app

ROOT = os.path.join(os.path.dirname(__file__), '..')


@pytest.fixture(scope='module')
def fix():
    spec = importlib.util.spec_from_file_location('fix_logos', os.path.join(ROOT, 'tools', 'fix_logos.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    app.config['TESTING'] = True
    with app.app_context():
        yield app.test_client()


# ── Miniaturas (tools/fix_logos.py) ──────────────────────────────────

@pytest.mark.parametrize('size', [(606, 600), (456, 600), (199, 278), (1358, 600), (556, 203), (50, 40)])
def test_la_miniatura_cabe_en_la_caja_conserva_la_proporcion_y_no_agranda(fix, size):
    image = Image.new('RGBA', size, (10, 80, 160, 255))
    (width, height), data = fix.build_thumb(image)
    assert width <= fix.THUMB_BOX[0] and height <= fix.THUMB_BOX[1]
    assert abs(width / height - size[0] / size[1]) / (size[0] / size[1]) < 0.01          # sin deformarla
    assert (width, height) == size or max(width / fix.THUMB_BOX[0], height / fix.THUMB_BOX[1]) > 0.99
    assert width <= size[0] and height <= size[1]
    assert Image.open(io.BytesIO(data)).format == 'WEBP'


def test_la_miniatura_es_sin_perdida_y_conserva_la_transparencia(fix):
    image = Image.new('RGBA', (160, 90), (0, 0, 0, 0))
    image.paste((200, 30, 30, 255), (40, 20, 120, 70))
    _, data = fix.build_thumb(image)
    decoded = Image.open(io.BytesIO(data)).convert('RGBA')
    assert decoded.size == image.size                                   # ya cabe: se conserva tal cual
    assert decoded.getpixel((0, 0))[3] == 0                             # el fondo sigue transparente
    assert decoded.getpixel((80, 45)) == (200, 30, 30, 255)             # y el color no cambió


def test_write_thumb_crea_actualiza_y_en_check_no_escribe(fix, tmp_path, monkeypatch):
    monkeypatch.setattr(fix, 'THUMB_DIR', str(tmp_path / 'thumbs'))
    logo = tmp_path / 'universidad_de_prueba.png'
    image = Image.new('RGBA', (600, 300), (0, 120, 0, 255))
    image.save(logo)
    assert fix.write_thumb(image, str(logo), check_only=True)[2] == 'falta'
    assert not (tmp_path / 'thumbs').exists()                                       # --check no escribe nada
    assert fix.write_thumb(image, str(logo), check_only=False)[2] == 'falta'        # la crea
    target = tmp_path / 'thumbs' / 'universidad_de_prueba.webp'
    assert target.is_file() and Image.open(target).size == (320, 160)
    assert fix.write_thumb(image, str(logo), check_only=False)[2] == 'ya estaba bien'   # idempotente
    other = Image.new('RGBA', (600, 300), (120, 0, 0, 255))
    assert fix.write_thumb(other, str(logo), check_only=True)[2] == 'desactualizada'


def test_cada_logo_del_proyecto_tiene_su_miniatura_al_dia():
    """Si falla: agregaste o cambiaste un logo; corre `python tools/fix_logos.py` y vuelve a probar."""
    spec = importlib.util.spec_from_file_location('fix_logos_repo', os.path.join(ROOT, 'tools', 'fix_logos.py'))
    fix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fix)
    pending = []
    for name in sorted(os.listdir(fix.LOGO_DIR)):
        path = os.path.join(fix.LOGO_DIR, name)
        if not os.path.isfile(path) or not name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            continue
        image, *_ = fix.process_raster(path)
        if fix.write_thumb(image, path, check_only=True)[2] != 'ya estaba bien':
            pending.append(name)
    assert not pending, f'Faltan o están desactualizadas las miniaturas de: {pending}. Corre: python tools/fix_logos.py'


# ── La landing usa las miniaturas ────────────────────────────────────

def test_thumb_path_devuelve_la_miniatura_o_el_logo_si_no_existe():
    assert landing.thumb_path('logos/universidad_central_de_venezuela.webp') == 'logos/thumbs/universidad_central_de_venezuela.webp'
    assert landing.thumb_path('logos/universidad_inexistente.png') == 'logos/universidad_inexistente.png'


def test_cada_institucion_trae_logo_completo_y_miniatura():
    for item in landing.universities():
        assert item['filename'].startswith('logos/') and item['thumb'].startswith('logos/')
        assert os.path.isfile(os.path.join(ROOT, 'static', item['thumb']))
        assert item['thumb'] == landing.thumb_path(item['filename'])


def test_la_landing_sirve_solo_miniaturas(client):
    page = client.get('/').get_data(as_text=True)
    assert 'logos/thumbs/universidad_central_de_venezuela.webp' in page          # logo inicial de la hoja
    strip = page.split('<div class="logos-strip reveal">', 1)[1].split('</div>', 1)[0]
    assert strip.count('<img ') == 5
    for stem in ('universidad_central_de_venezuela', 'universidad_de_carabobo', 'universidad_de_los_andes',
                 'universidad_del_zulia', 'universidad_nacional_experimental_romulo_gallegos'):
        assert f'logos/thumbs/{stem}.webp' in strip
    # ninguna etiqueta <img> apunta al logo completo (Word lo sigue usando; la web no)
    for src in re.findall(r'<img[^>]+src="([^"]+)"', page):
        assert '/logos/' not in src or '/logos/thumbs/' in src, src


def test_el_json_de_la_rotacion_incluye_la_miniatura(client):
    page = client.get('/').get_data(as_text=True)
    json_block = re.search(r'id="preview-universities" type="application/json">(.*?)</script>', page, re.S).group(1)
    assert '\\u0022thumb' in json_block or '"thumb"' in json_block


# ── Animación del papel: solo transform ──────────────────────────────

def test_el_brillo_del_papel_se_anima_con_transform_no_con_background_position():
    css = open(os.path.join(ROOT, 'static', 'css', 'landing.css'), encoding='utf-8').read()
    assert 'animation: paperShine' in css
    keyframes = re.search(r'@keyframes paperShine\s*\{(.*?)\}\s*\}', css, re.S).group(1)
    assert 'transform' in keyframes and 'background' not in keyframes
    assert 'animation: shimmer' not in css                  # el viejo repintaba toda la tarjeta en cada cuadro
    assert re.search(r'\.pm-university\s*\{[^}]*will-change:\s*opacity', css)


def test_el_script_de_la_rotacion_espera_la_decodificacion_antes_de_mostrar():
    js = open(os.path.join(ROOT, 'static', 'js', 'landing-preview.js'), encoding='utf-8').read()
    assert 'university.thumb' in js                                       # usa la miniatura
    assert js.count("classList.remove('is-changing')") == 1               # el bloque solo se vuelve a mostrar en reveal()
    assert 'logo.decode().then(reveal, reveal)' in js                     # ...cuando el logo ya está decodificado
    assert 'window.setTimeout(reveal, DECODE_WAIT_MS)' in js              # y nunca queda atascado si no responde


# ── Vista previa del formulario ──────────────────────────────────────

def test_la_vista_previa_del_formulario_prueba_primero_la_miniatura():
    js = open(os.path.join(ROOT, 'static', 'js', 'builder.js'), encoding='utf-8').read()
    start = js.index('function renderCrest()')
    body = js[start:js.index('tryNext();', start)]
    assert body.index("'/thumbs/' + slug + '.webp'") < body.index("LOGO_EXT.map")      # miniatura antes que el logo completo
    assert 'candidates[i++]' in body and 'LOGO_EXT.length' not in body                # recorre la lista completa


def test_la_miniatura_que_pide_el_formulario_se_sirve(client):
    for item in landing.universities():
        base = '/static/logos'                                   # data-logo-base del formulario
        slug = item['id']
        assert client.get(f'{base}/thumbs/{slug}.webp').status_code == 200
        assert client.get('/static/' + item['filename']).status_code == 200          # y el logo completo sigue ahí
