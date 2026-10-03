# rate_limit.py
"""Protección anti-DDoS con rate limiting en memoria (sliding window).

Diseñado para un solo worker con varios hilos (gunicorn --workers 1 --threads 8).
No necesita Redis ni dependencias externas: usa diccionarios protegidos con Lock.

Variables de entorno opcionales:
    RATE_LIMIT_ENABLED  – '0' para desactivar todo el rate limiting (default: '1')
    RATE_LIMIT_GLOBAL   – máx. solicitudes/minuto por IP (default: 120)
"""
import logging
import os
import threading
import time
from functools import wraps

from flask import request, jsonify, render_template, g

log = logging.getLogger(__name__)

# ── Sliding-window store ──────────────────────────────────────────────

_store: dict[str, list[float]] = {}
_lock = threading.Lock()


def _is_enabled():
    return os.getenv('RATE_LIMIT_ENABLED', '1') != '0'


def _hit(key: str, max_requests: int, window: float) -> tuple[bool, int]:
    """Registra un hit y devuelve (allowed, retry_after_seconds).

    Thread-safe gracias a _lock.  Las entradas expiradas se limpian inline
    para la clave consultada; un hilo de fondo barre el resto.
    """
    now = time.time()
    cutoff = now - window
    with _lock:
        timestamps = _store.get(key)
        if timestamps is None:
            timestamps = []
            _store[key] = timestamps
        # Purgar entradas fuera de la ventana (in-place para O(n) amortizado).
        while timestamps and timestamps[0] <= cutoff:
            timestamps.pop(0)
        if len(timestamps) >= max_requests:
            retry_after = int(timestamps[0] - cutoff) + 1
            return False, max(retry_after, 1)
        timestamps.append(now)
        return True, 0


def _purge_expired(max_window: float = 120.0):
    """Elimina claves cuyas marcas están todas fuera de la ventana más ancha."""
    cutoff = time.time() - max_window
    with _lock:
        dead = [k for k, ts in _store.items() if not ts or ts[-1] <= cutoff]
        for k in dead:
            del _store[k]


# ── Cleanup daemon ────────────────────────────────────────────────────

_cleanup_started = False


def _start_cleanup():
    global _cleanup_started
    if _cleanup_started:
        return
    _cleanup_started = True

    def loop():
        while True:
            time.sleep(60)
            _purge_expired()

    t = threading.Thread(target=loop, daemon=True, name='rate-limit-cleanup')
    t.start()


# ── Response helpers ──────────────────────────────────────────────────

def _too_many_requests(retry_after: int):
    """Devuelve JSON o HTML según el tipo de petición."""
    message = f'Demasiadas solicitudes. Intenta de nuevo en {retry_after} segundos.'
    if _wants_json():
        resp = jsonify(error=message)
    else:
        try:
            resp = render_template('429.html', retry_after=retry_after)
            from flask import make_response
            resp = make_response(resp)
        except Exception:
            from flask import make_response
            resp = make_response(message)
    resp.status_code = 429
    resp.headers['Retry-After'] = str(retry_after)
    return resp


def _wants_json():
    """¿La petición espera JSON? (XHR, fetch, Content-Type JSON)."""
    if request.is_json:
        return True
    accept = request.headers.get('Accept', '')
    if 'application/json' in accept and 'text/html' not in accept:
        return True
    if request.headers.get('X-Requested-With', '').lower() == 'xmlhttprequest':
        return True
    return False


# ── Client IP helper ─────────────────────────────────────────────────

def _client_ip():
    """IP real del cliente.  Con ProxyFix activo request.remote_addr ya es
    la IP del X-Forwarded-For; sin proxy es la directa.
    """
    return request.remote_addr or '0.0.0.0'


# ── Decorator for per-route limits ───────────────────────────────────

def rate_limit(max_requests: int = 10, window: int = 60, *, key_func=None):
    """Decorador que aplica un límite específico a una vista.

    Args:
        max_requests: número máximo de hits dentro de la ventana.
        window: duración de la ventana en segundos.
        key_func: callable que devuelve la clave de throttle.
                  Por defecto se usa 'route:<endpoint>:<ip>'.
                  Para limitar por usuario pasar key_func=per_user.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not _is_enabled():
                return view(*args, **kwargs)
            # Admins exentos.
            if _is_admin():
                return view(*args, **kwargs)
            if key_func is not None:
                key = key_func()
            else:
                key = f'route:{view.__name__}:{_client_ip()}'
            allowed, retry_after = _hit(key, max_requests, window)
            if not allowed:
                log.warning('Rate limit (ruta %s): %s bloqueado por %ds',
                            view.__name__, key, retry_after)
                return _too_many_requests(retry_after)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def per_user():
    """Key que identifica al usuario logueado (fallback a IP)."""
    from auth import current_user
    user = current_user()
    if user:
        return f'user:{user["id"]}:{request.endpoint}'
    return f'ip:{_client_ip()}:{request.endpoint}'


def per_ip():
    """Key basada sólo en la IP (para rutas públicas de auth)."""
    return f'ip:{_client_ip()}:{request.endpoint}'


# ── Admin check ───────────────────────────────────────────────────────

def _is_admin():
    """Revisa si el usuario actual es admin (exento de rate limits)."""
    try:
        from auth import current_user
        user = current_user()
        return user and user.get('is_admin')
    except Exception:
        return False


# ── App initialization (global per-IP limit) ─────────────────────────

def init_app(app):
    """Registra el rate limit global por IP como before_request."""

    @app.before_request
    def _global_rate_limit():
        if not _is_enabled():
            return None
        # Evaluar en cada petición útil para los tests
        global_limit = int(os.getenv('RATE_LIMIT_GLOBAL', '120'))
        # Admins exentos del límite global también.
        if _is_admin():
            return None
        # Archivos estáticos no cuentan.
        if request.path.startswith('/static/'):
            return None
        key = f'global:{_client_ip()}'
        allowed, retry_after = _hit(key, global_limit, 60)
        if not allowed:
            log.warning('Rate limit global: %s bloqueado por %ds',
                        _client_ip(), retry_after)
            return _too_many_requests(retry_after)
        return None

    @app.errorhandler(429)
    def handle_429(e):
        return _too_many_requests(60)

    if _is_enabled():
        log.info('Rate limiting inicializado activo')
    _start_cleanup()


# ── Testing helpers ───────────────────────────────────────────────────

def _reset():
    """Limpiar el store (solo para tests)."""
    with _lock:
        _store.clear()
