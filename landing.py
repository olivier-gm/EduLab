"""Contenido de la portada de muestra; sin llamadas a IA."""
import json
import random
import time
from pathlib import Path

import db
from algorythms import Document_process

ROOT = Path(__file__).resolve().parent
TITLE_PERIOD = 4 * 60 * 60


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
                result.append({'id': slug, 'name': name, 'filename': filename})
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
