# retention.py
"""Limpieza automática de los archivos generados (output/*.docx y *.pdf).

Cada documento guarda en la base de datos cuándo vence (db.FILE_RETENTION_HOURS
después de generarse). Un hilo en segundo plano revisa cada pocos minutos y
borra lo vencido. Antes se programaba un threading.Timer al abrir la pantalla
de descarga: se perdía si el servidor se reiniciaba, se acumulaba un timer por
cada visita y los archivos que nadie abría nunca se borraban.
"""

import logging
import os
import sqlite3
import threading
import time

import db

logger = logging.getLogger(__name__)

OUTPUT_DIR = 'output'
EXTENSIONS = ('.docx', '.pdf')
SWEEP_INTERVAL = 15 * 60      # segundos entre revisiones


def _remove(stem):
    for ext in EXTENSIONS:
        path = os.path.join(OUTPUT_DIR, stem + ext)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning('No se pudo borrar %s: %s', path, e)


def cleanup_expired_files(now=None):
    """Borra los archivos vencidos y devuelve cuántos documentos limpió.

    1) Los que la base de datos marca como vencidos (y les quita el nombre de
       archivo, para no volver a procesarlos).
    2) Los archivos sueltos de output/ más viejos que el plazo que nadie
       registró (documentos anteriores a "Mis informes", pruebas, restos).
    """
    now = now or db._utcnow()
    limit = now.strftime(db.DATETIME_FMT)
    cleaned = 0

    conn = sqlite3.connect(db.DB_PATH)
    try:
        rows = conn.execute(
            'SELECT id, file_stem FROM documents WHERE file_stem IS NOT NULL AND expires_at <= ?',
            (limit,),
        ).fetchall()
        for doc_id, stem in rows:
            _remove(stem)
            conn.execute('UPDATE documents SET file_stem = NULL WHERE id = ?', (doc_id,))
            cleaned += 1
        conn.commit()

        # Archivos vigentes según la base de datos: nunca se tocan por antigüedad.
        active = {
            row[0] for row in conn.execute(
                'SELECT file_stem FROM documents WHERE file_stem IS NOT NULL AND expires_at > ?',
                (limit,),
            )
        }
    finally:
        conn.close()

    # mtime es hora real (epoch), así que se compara contra el reloj real.
    real_cutoff = time.time() - db.FILE_RETENTION_HOURS * 3600
    if os.path.isdir(OUTPUT_DIR):
        for name in os.listdir(OUTPUT_DIR):
            stem, ext = os.path.splitext(name)
            if ext.lower() not in EXTENSIONS or stem in active:
                continue
            path = os.path.join(OUTPUT_DIR, name)
            try:
                if os.path.getmtime(path) < real_cutoff:
                    os.remove(path)
                    cleaned += 1
            except OSError as e:
                logger.warning('No se pudo revisar/borrar %s: %s', path, e)

    if cleaned:
        logger.info('Limpieza automática: %d archivo(s)/documento(s) vencidos borrados', cleaned)
    return cleaned


def _loop():
    while True:
        try:
            cleanup_expired_files()
        except Exception:
            logger.exception('Falló la limpieza automática de archivos')
        time.sleep(SWEEP_INTERVAL)


_started = False


def start_background_cleanup():
    """Arranca (una sola vez por proceso) el hilo de limpieza."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name='file-retention', daemon=True).start()
