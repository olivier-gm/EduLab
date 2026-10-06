# -*- coding: utf-8 -*-
"""Deja los logos de universidades listos para la portada.

    python tools/fix_logos.py                 # arregla todos los de static/logos
    python tools/fix_logos.py ruta/logo.png   # solo ese archivo
    python tools/fix_logos.py --check         # solo informa, no modifica nada

La portada dibuja TODOS los logos con el mismo alto (ver Document_process.LOGO_HEIGHT_CM)
y el ancho sale de la proporción del archivo. Por eso lo único que importa de cada
archivo es que la imagen visible ocupe todo el lienzo: si trae márgenes vacíos
(transparentes o del color del fondo), el escudo se ve más pequeño que los demás aunque
midan igual. El script, por cada logo:

  1. Recorta los márgenes vacíos (transparencia, o un borde del color del fondo).
  2. Reduce la altura a 600 px como máximo (más no hace falta: 3 cm a 500 dpi).
  3. Lo guarda con el mismo formato y nombre (png, webp sin pérdida, jpg).
  4. Avisa si la imagen es muy pequeña, o tan ancha o tan alta que se vea fuera de
     proporción con los demás.
  5. Crea su miniatura para la web en static/logos/thumbs/ (WebP SIN pérdida, como máximo
     320 x 180 px y con la misma proporción). La landing usa solo las miniaturas: el
     navegador baja ~25 KB en vez de ~180 KB y decodifica 10 veces menos píxeles, así el
     cambio de logo no se traba. Word sigue usando el logo completo.

Es seguro correrlo varias veces: si un logo ya está bien, no lo toca.
Los .svg no se modifican (son vectoriales, no necesitan miniatura); solo se informa su proporción.
"""
import argparse
import io
import os
import re
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOGO_DIR = os.path.join(ROOT, 'static', 'logos')

from algorythms import Document_process  # noqa: E402

HEIGHT_CM = Document_process.LOGO_HEIGHT_CM
MAX_WIDTH_CM = Document_process.LOGO_MAX_WIDTH_CM
TARGET_HEIGHT_PX = 600
MIN_HEIGHT_PX = 236            # 3 cm a 200 dpi: por debajo se verá pixelado al imprimir
ALPHA_THRESHOLD = 8            # alfa por debajo de esto cuenta como "vacío"
BACKGROUND_TOLERANCE = 12      # diferencia de color que se considera "el mismo fondo"
EXTREME_RATIOS = (0.4, 3.5)    # ancho/alto fuera de este rango se avisa
EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.svg')
THUMB_DIR = os.path.join(LOGO_DIR, 'thumbs')
# Máximo ancho x alto de la miniatura: ~3 veces el escudo de la portada de muestra (72 px) y de la
# franja de logos (58 px de alto), así se ve nítida hasta en pantallas de alta densidad.
THUMB_BOX = (320, 180)


def content_box(image):
    """Caja (izq, arriba, der, abajo) con lo que se ve de verdad, o None si no hay nada."""
    rgba = image.convert('RGBA')
    alpha = rgba.getchannel('A')
    low, _ = alpha.getextrema()
    if low < 255:                                    # tiene transparencia: se recorta lo transparente
        return alpha.point(lambda a: 255 if a > ALPHA_THRESHOLD else 0).getbbox()
    # Opaco: se recorta el borde que tiene el mismo color que la esquina (el fondo).
    corner = rgba.getpixel((0, 0))[:3]
    background = Image.new('RGB', rgba.size, corner)
    from PIL import ImageChops
    diff = ImageChops.difference(rgba.convert('RGB'), background).convert('L')
    return diff.point(lambda d: 255 if d > BACKGROUND_TOLERANCE else 0).getbbox()


def process_raster(path):
    """(imagen final, tamaño antes, avisos, ¿cambió?) sin escribir nada. La imagen es None si está vacía."""
    with Image.open(path) as source:
        source.load()
        image = source.copy()
    notes = []
    before = image.size
    box = content_box(image)
    if box is None:
        return None, before, ['ERROR: la imagen está vacía'], False
    changed = False
    if box != (0, 0, *image.size):
        image = image.crop(box)
        changed = True
        notes.append('recortados los márgenes vacíos')
    if image.height > TARGET_HEIGHT_PX:
        width = max(1, round(image.width * TARGET_HEIGHT_PX / image.height))
        image = image.resize((width, TARGET_HEIGHT_PX), Image.LANCZOS)
        changed = True
        notes.append(f'reducida a {TARGET_HEIGHT_PX} px de alto')
    if image.height < MIN_HEIGHT_PX:
        notes.append(f'AVISO: solo {image.height} px de alto; se verá pixelado al imprimir '
                     f'(conviene un archivo de al menos {MIN_HEIGHT_PX} px de alto)')
    return image, before, notes, changed


def save_raster(path, image):
    extension = os.path.splitext(path)[1].lower()
    if extension == '.png':
        image.save(path, format='PNG', optimize=True)
    elif extension == '.webp':
        image.save(path, format='WEBP', lossless=True, method=6)
    else:
        image.convert('RGB').save(path, format='JPEG', quality=95, optimize=True)


def fix_raster(path, check_only):
    image, before, notes, changed = process_raster(path)
    if image is None:
        return before, before, notes, False
    if changed and not check_only:
        save_raster(path, image)
    return before, image.size, notes, changed


def thumb_path(path):
    return os.path.join(THUMB_DIR, os.path.splitext(os.path.basename(path))[0] + '.webp')


def build_thumb(image):
    """(tamaño, bytes) de la miniatura WebP sin pérdida: cabe en THUMB_BOX, misma proporción, sin agrandar."""
    rgba = image.convert('RGBA')
    scale = min(THUMB_BOX[0] / rgba.width, THUMB_BOX[1] / rgba.height, 1)
    size = (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale)))
    if size != rgba.size:
        rgba = rgba.resize(size, Image.LANCZOS)        # Pillow remuestrea con alfa premultiplicado: sin halos
    buffer = io.BytesIO()
    rgba.save(buffer, format='WEBP', lossless=True, method=6)
    return size, buffer.getvalue()


def write_thumb(image, path, check_only):
    """Crea o actualiza la miniatura de `path`. Devuelve (tamaño, bytes, estado): 'ya estaba bien',
    'falta' o 'desactualizada' (en --check solo se informa)."""
    size, data = build_thumb(image)
    target = thumb_path(path)
    current = None
    if os.path.isfile(target):
        with open(target, 'rb') as stream:
            current = stream.read()
    state = 'ya estaba bien' if current == data else ('falta' if current is None else 'desactualizada')
    if state != 'ya estaba bien' and not check_only:
        os.makedirs(THUMB_DIR, exist_ok=True)
        with open(target, 'wb') as stream:
            stream.write(data)
    return size, len(data), state


def svg_ratio(path):
    text = open(path, encoding='utf-8', errors='ignore').read(4000)
    box = re.search(r'viewBox\s*=\s*"\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)\s*"', text)
    if box:
        return float(box.group(1)) / float(box.group(2))
    width, height = (re.search(rf'\b{name}\s*=\s*"([\d.]+)', text) for name in ('width', 'height'))
    return float(width.group(1)) / float(height.group(1)) if width and height else None


def describe(ratio):
    """(ancho final en cm, avisos de proporción) para un logo dibujado a 3 cm de alto."""
    width_cm = HEIGHT_CM * ratio
    notes = []
    if width_cm > MAX_WIDTH_CM:
        notes.append(f'AVISO: a {HEIGHT_CM:g} cm de alto mediría {width_cm:.1f} cm de ancho; la portada lo limita a '
                     f'{MAX_WIDTH_CM:g} cm y quedará a {MAX_WIDTH_CM / ratio:.1f} cm de alto')
        width_cm = MAX_WIDTH_CM
    if not EXTREME_RATIOS[0] <= ratio <= EXTREME_RATIOS[1]:
        notes.append(f'AVISO: proporción {ratio:.2f} (ancho/alto) muy extrema; revisa que el archivo no tenga '
                     'texto o espacio de más')
    return width_cm, notes


def main():
    if hasattr(sys.stdout, 'reconfigure'):          # consolas de Windows con codificación antigua
        sys.stdout.reconfigure(errors='replace')
    parser = argparse.ArgumentParser(description='Deja los logos de universidades listos para la portada.')
    parser.add_argument('files', nargs='*', help='logos a revisar (por defecto, todos los de static/logos)')
    parser.add_argument('--check', action='store_true', help='solo informar, sin modificar archivos')
    args = parser.parse_args()

    files = args.files or sorted(
        os.path.join(LOGO_DIR, name) for name in os.listdir(LOGO_DIR)
        if name.lower().endswith(EXTENSIONS))
    if not files:
        print(f'No hay logos en {LOGO_DIR}')
        return 0

    problems = 0
    for path in files:
        name = os.path.basename(path)
        if not os.path.isfile(path) or not path.lower().endswith(EXTENSIONS):
            print(f'- {name}: no es un logo (usa png, jpg, webp o svg)')
            problems += 1
            continue
        if not re.fullmatch(r'[a-z0-9_]+', os.path.splitext(name)[0]):
            print(f'  AVISO {name}: el nombre debe ser el de la universidad en minúsculas, sin tildes y con '
                  'guiones bajos (universidad_de_los_andes.png) para que la app lo encuentre')
            problems += 1
        if path.lower().endswith('.svg'):
            ratio = svg_ratio(path)
            if ratio is None:
                print(f'- {name}: svg sin proporción legible (usa viewBox)')
                problems += 1
                continue
            width_cm, notes = describe(ratio)
            print(f'- {name}: svg, proporción {ratio:.2f} -> {width_cm:.1f} x {HEIGHT_CM:g} cm en la portada')
        else:
            try:
                image, before, notes, changed = process_raster(path)
                if image is None:
                    print(f'- {name}: la imagen está vacía')
                    problems += 1
                    continue
                if changed and not args.check:
                    save_raster(path, image)
                after = image.size
                thumb_size, thumb_bytes, thumb_state = write_thumb(image, path, args.check)
            except Exception as error:
                print(f'- {name}: no se pudo leer la imagen ({error})')
                problems += 1
                continue
            width_cm, extra = describe(after[0] / after[1])
            notes = notes + extra
            action = ('necesita arreglo' if args.check else 'arreglado') if changed else 'ya estaba bien'
            sizes = f'{before[0]}x{before[1]}' + (f' -> {after[0]}x{after[1]}' if before != after else '')
            print(f'- {name}: {action} ({sizes} px) -> {width_cm:.1f} x {HEIGHT_CM:g} cm en la portada')
            if changed and args.check:
                problems += 1
            verb = {'ya estaba bien': 'ya estaba bien', 'falta': 'falta' if args.check else 'creada',
                    'desactualizada': 'desactualizada' if args.check else 'actualizada'}[thumb_state]
            print(f'    miniatura web: thumbs/{os.path.basename(thumb_path(path))} {thumb_size[0]}x{thumb_size[1]} px, '
                  f'{thumb_bytes / 1024:.0f} KB ({verb})')
            if args.check and thumb_state != 'ya estaba bien':
                problems += 1
        for note in notes:
            print(f'    {note}')
            if note.startswith(('AVISO', 'ERROR')):
                problems += 1
    if not args.files and os.path.isdir(THUMB_DIR):
        sources = {os.path.splitext(os.path.basename(f))[0] for f in files}
        for leftover in sorted(os.listdir(THUMB_DIR)):
            if os.path.splitext(leftover)[0] not in sources:
                print(f'  AVISO thumbs/{leftover}: ya no existe su logo; puedes borrar la miniatura')
                problems += 1
    print()
    print('Todo en orden.' if not problems else f'{problems} punto(s) a revisar.')
    return 1 if (args.check and problems) else 0


if __name__ == '__main__':
    sys.exit(main())
