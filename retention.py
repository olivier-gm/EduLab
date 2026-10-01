# retention.py
"""Limpieza automática de los archivos generados (.docx y .pdf, en R2 o en output/).

Cada documento guarda en la base de datos cuándo vence (db.FILE_RETENTION_HOURS
después de generarse). Un hilo en segundo plano revisa cada pocos minutos y
borra lo vencido. Antes se programaba un threading.Timer al abrir la pantalla
de descarga: se perdía si el servidor se reiniciaba, se acumulaba un timer por
cada visita y los archivos que nadie abría nunca se borraban.
"""

import logging
import threading
import time

import db
import storage

logger = logging.getLogger(__name__)

SWEEP_INTERVAL = 15 * 60      # segundos entre revisiones
SCRATCH_MAX_AGE = 3600        # restos de generaciones fallidas en output/ (solo con R2)


def cleanup_expired_files(now=None):
    """Borra los archivos vencidos y devuelve cuántos documentos limpió.

    1) Los que la base de datos marca como vencidos (y les quita el nombre de
       archivo, para no volver a procesarlos).
    2) Los archivos sueltos del almacenamiento más viejos que el plazo que nadie
       registró (documentos anteriores a "Mis informes", pruebas, restos).
    3) Restos de trabajo en la carpeta local cuando el almacenamiento es R2.
    """
    cleaned = 0

    for doc_id, stem in db.expired_documents(now):
        storage.delete(stem)
        db.detach_file(doc_id)
        cleaned += 1

    # Archivos vigentes según la base de datos: nunca se tocan por antigüedad.
    active = db.active_file_stems(now)
    # La fecha de modificación es hora real (epoch), así que se compara contra el reloj real.
    real_cutoff = time.time() - db.FILE_RETENTION_HOURS * 3600
    for item in storage.list_files():
        if item['stem'] in active or item['modified'] >= real_cutoff:
            continue
        try:
            storage.delete_file(item['stem'], item['ext'])
            cleaned += 1
        except Exception as e:
            logger.warning('No se pudo borrar %s.%s: %s', item['stem'], item['ext'], e)

    storage.local_scratch_cleanup(SCRATCH_MAX_AGE)

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
