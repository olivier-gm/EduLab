# jobs.py
"""Ejecuta las generaciones de documentos en segundo plano.

Un informe con un modelo lento puede tardar varios minutos, y los proxies (Azure corta a los ~230 s)
cierran la conexión antes: el usuario veía un 504 aunque el servidor terminara bien. Ahora la petición
que inicia la generación termina al instante y el trabajo corre en un hilo de este mismo proceso; la
página de espera consulta su avance en la tabla `generation_jobs` (db.py), así que cualquier instancia
puede responder y el avance sobrevive a que el navegador se cierre.

Mientras corre, un segundo hilo "late" cada HEARTBEAT_SECONDS. Si el proceso muere, el trabajo deja de
latir y db.reap_stale_jobs() lo cierra devolviendo el cupo.

En las pruebas (config GENERATION_INLINE) el trabajo corre en el acto, dentro de la petición, para que
`follow_redirects` llegue hasta la pantalla de descarga sin esperar hilos.
"""
import contextvars
import logging
import os
import threading

import db

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30
MAX_CONCURRENT = int(os.environ.get('GENERATION_MAX_CONCURRENT', '8'))

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


def try_acquire():
    """Reserva un lugar para una generación. False si ya hay demasiadas a la vez."""
    return _slots.acquire(blocking=False)


def release():
    try:
        _slots.release()
    except ValueError:        # liberado de más: no debe tumbar nada
        logger.warning('Se liberó un lugar de generación que no estaba reservado.')


def start(app, token, work):
    """Corre `work()` dentro de un contexto de aplicación. Debe haberse llamado a try_acquire() antes;
    el lugar se libera al terminar."""
    def run():
        stop = threading.Event()
        try:
            with app.app_context():
                if not app.config.get('GENERATION_INLINE'):
                    threading.Thread(target=_heartbeat, args=(app, token, stop), name=f'heartbeat-{token[:6]}',
                                     daemon=True).start()
                work()
        except Exception:
            logger.exception('El trabajo de generación %s terminó con un error no controlado', token)
        finally:
            stop.set()
            release()

    if app.config.get('GENERATION_INLINE'):
        contextvars.copy_context().run(run)
        return
    threading.Thread(target=run, name=f'generation-{token[:6]}', daemon=True).start()


def _heartbeat(app, token, stop):
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            with app.app_context():
                db.touch_job(token)
        except Exception:
            logger.warning('No se pudo registrar el latido del trabajo %s', token, exc_info=True)
