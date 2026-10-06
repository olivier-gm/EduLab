"""Contenido de la portada de muestra; sin llamadas a IA."""
import json
import random
import time
from pathlib import Path

import db
from algorythms import Document_process

ROOT = Path(__file__).resolve().parent
TITLE_PERIOD = 4 * 60 * 60


def thumb_path(filename):
    """Ruta (dentro de static) de la miniatura web de un logo, o el propio logo si todavía no la tiene.

    tools/fix_logos.py crea static/logos/thumbs/<nombre>.webp (WebP sin pérdida, máximo 320 x 180 px).
    La landing usa solo miniaturas: pesan ~25 KB en vez de ~180 KB y se decodifican en una fracción
    del tiempo, que es lo que evita que el cambio de logo se vea trabado."""
    candidate = f'logos/thumbs/{Path(filename).stem}.webp'
    return candidate if (ROOT / 'static' / candidate).is_file() else filename


def universities():
    """Instituciones con un logo local, usando la misma prioridad que Word."""
    names = (ROOT / 'static/txt/lista_imagenes.txt').read_text(encoding='utf-8').splitlines()
    result = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        slug = Document_process._normalize_logo_name(name)
        for extension in ('png', 'jpg', 'jpeg', 'webp', 'svg'):
            filename = f'logos/{slug}.{extension}'
            if (ROOT / 'static' / filename).is_file():
                result.append({'id': slug, 'name': name, 'filename': filename, 'thumb': thumb_path(filename)})
                break
    return result


def selected_ids(settings):
    try:
        values = json.loads(settings['landing_universities'])
        return {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()
    except (ValueError, KeyError):
        return set()


def preview(settings, now=None):
    selected = selected_ids(settings)
    institutions = [item for item in universities() if item['id'] in selected]
    titles = list(dict.fromkeys(line.strip() for line in settings['landing_titles'].splitlines() if line.strip()))
    if not titles:
        titles = ['El impacto de la inteligencia artificial']
    # Orden aleatorio reproducible: todas las instancias usan el mismo título.
    # Cada bloque avanza al siguiente; dos bloques consecutivos no se repiten.
    random.Random('\n'.join(titles)).shuffle(titles)
    bucket = int(time.time() if now is None else now) // TITLE_PERIOD
    return institutions, titles[bucket % len(titles)]
