# db.py
"""Acceso a la base de datos: usuarios, documentos, pagos y ajustes.

PostgreSQL (Supabase) cuando existe DATABASE_URL; archivo SQLite en otro caso
(desarrollo y pruebas). Se usa SQL directo, sin ORM. La conexión vive en el
contexto de la petición de Flask (flask.g).
"""

import os
import re
import sqlite3
import sys
import threading
import time
import logging
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import g

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('DATABASE_PATH', 'gullieth.db')

# PostgreSQL (Supabase): postgresql://usuario:clave@host:5432/postgres. Si está
# definida, DB_PATH se ignora. Con el pooler de Supabase usa el puerto 6543.
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
POOL_MAX = int(os.environ.get('DB_POOL_MAX', '5'))

# Horas que se conservan los archivos generados (.docx/.pdf) antes de borrarse.
# Valor inicial y plazo para archivos huérfanos. El panel cambia el plazo de
# documentos nuevos; cada documento registrado conserva su propio expires_at.
FILE_RETENTION_HOURS = int(os.environ.get('FILE_RETENTION_HOURS', '24'))

# Correos que se marcan como administradores automáticamente al registrarse
# o iniciar sesión (no hay UI para promover usuarios a admin: se resuelve
# por variable de entorno). Formato: "correo1@x.com,correo2@y.com".
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get('ADMIN_EMAILS', '').split(',')
    if e.strip()
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            {PK},
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    google_id     TEXT UNIQUE,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    plan          TEXT NOT NULL DEFAULT 'free',
    created_at    TEXT NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS documents (
    id           {PK},
    user_id      INTEGER NOT NULL REFERENCES users(id),
    title        TEXT NOT NULL,
    doc_type     TEXT NOT NULL,
    tokens_used  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT {NOW}
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);

-- Ajustes editables desde el panel admin (clave/valor).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Pagos del plan: el usuario reporta su referencia y el admin lo aprueba.
CREATE TABLE IF NOT EXISTS payments (
    id          {PK},
    user_id     INTEGER NOT NULL REFERENCES users(id),
    method      TEXT NOT NULL,            -- 'binance' | 'pago_movil'
    reference   TEXT NOT NULL,
    amount_usd  DOUBLE PRECISION NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created_at  TEXT NOT NULL DEFAULT {NOW},
    reviewed_at TEXT,
    reviewed_by INTEGER REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);

-- Solicitudes de registro: nunca son usuarios ni permiten iniciar sesión.
CREATE TABLE IF NOT EXISTS pending_registrations (
    email TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    sent_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    sends INTEGER NOT NULL,
    window_at INTEGER NOT NULL,
    ip_hash TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'register',
    verified_until INTEGER NOT NULL DEFAULT 0
);
"""

# Valores por defecto de los ajustes. Vacío en un límite = sin límite.
SETTING_DEFAULTS = {
    'file_retention_hours': str(FILE_RETENTION_HOURS),
    # Usuarios SIN plan activo: por cada modo, si pueden generar y cuántos
    # documentos en total por usuario.
    'free_ai_enabled': '1',
    'free_ai_limit': '',
    'free_manual_enabled': '1',
    'free_manual_limit': '',
    # Plan de pago (uno solo por ahora)
    'plan_name': 'Plan Premium',
    'plan_price_usd': '5',
    'plan_days': '30',
    # Datos de cobro (se completan desde el panel admin)
    'binance_email': '',
    'pm_bank': 'BDV · 0102',
    'pm_phone': '',
    'pm_id': '',
    'pm_holder': '',
    'bs_rate': '',   # Bs por dólar, opcional: muestra el monto en Bs
    # Origen de la última bibliografía automática completada (no editable).
    'bibliography_source': '',
    'bibliography_reason': '',
    'bibliography_updated_at': '',
    'bibliography_provider': '',
    'bibliography_model': '',
    'ai_provider': 'gemini',
    # Si el proveedor activo falla (cuota, caída, error desconocido), reintenta
    # con el otro. Necesita la clave de los dos proveedores.
    'fallback_enabled': '0',
    # Google Search / búsqueda web de cada proveedor para el desarrollo y la
    # bibliografía. Si la búsqueda falla se genera igual sin ella.
    'gemini_search_enabled': '1',
    'openrouter_search_enabled': '1',
    'gemini_model': '',  # Vacío conserva el modelo del entorno actual.
    'openrouter_model': 'google/gemini-3.8-flash',
    # Modelos más ligeros para tareas pequeñas (mayúsculas y tildes del título). Vacío = el principal.
    'gemini_light_model': '',
    'openrouter_light_model': '',
    'gemini_api_key': '',  # Cifradas; nunca se rellenan en el HTML.
    'openrouter_api_key': '',
}

PLAN_IDS = ('recharge', 'premium', 'pro')
for _id, _name, _price, _limit, _terms, _hours, _benefits in (
    ('recharge', 'Recarga', '2.99', 20, 100, 1, 'Informes con IA\nDocumentos manuales\nDescarga solo en Word\nSin acceso a glosarios\nSaldo sin vencimiento; se pausa con un plan mensual'),
    ('premium', 'Premium', '4.99', 400, 100, 72, 'Informes y glosarios con IA\nDocumentos manuales\nUniversitario y bachillerato\nDescarga en Word y PDF'),
    ('pro', 'Pro', '14.99', 2000, 300, 8760, 'Generaciones ilimitadas\nInformes y glosarios con IA\nDocumentos manuales\nUniversitario y bachillerato\nDescarga en Word y PDF'),
):
    for _key, _value in {'name': _name, 'price': _price, 'limit': _limit, 'terms': _terms,
                         'hours': _hours, 'enabled': 1, 'benefits': _benefits}.items():
        SETTING_DEFAULTS[f'{_id}_{_key}'] = str(_value)


def plan_catalog(settings=None):
    settings = settings or get_settings()
    return {key: {'id': key, 'name': settings[f'{key}_name'], 'price': settings[f'{key}_price'],
                  'enabled': settings[f'{key}_enabled'] == '1',
                  'limit': int(settings[f'{key}_limit']), 'terms': int(settings[f'{key}_terms']),
                  'hours': int(settings[f'{key}_hours']),
                  'benefits': settings[f'{key}_benefits'].splitlines()}
            for key in PLAN_IDS}

# ── Conexión: SQLite (desarrollo) o PostgreSQL (Supabase) ─────────────
#
# El resto de la app solo usa las funciones públicas de este módulo. Con
# DATABASE_URL (postgresql://...) todo va a PostgreSQL; sin ella se usa el
# archivo SQLite DB_PATH, que sirve para desarrollo local y pruebas.
#
# Para no mantener dos versiones de cada consulta se escribe SQL con "?" y se
# traduce al vuelo para PostgreSQL (ver to_pg). Las fechas se guardan como texto
# 'YYYY-MM-DD HH:MM:SS' en UTC en ambos motores, así que las comparaciones y el
# formato no cambian entre uno y otro.

_pool = None
_pool_lock = threading.Lock()
_VALIDATE_PG = False     # las pruebas lo activan: valida con pglast cada consulta


def is_postgres():
    return DATABASE_URL.startswith(('postgres://', 'postgresql://'))


class Row(dict):
    """Fila de PostgreSQL con acceso por nombre y por posición (como sqlite3.Row)."""
    __slots__ = ()

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]

    def make(values):
        return Row(zip(names, values))
    return make


def to_pg(sql):
    """Traduce una consulta escrita con '?' a PostgreSQL.

    Devuelve (sql, wants_id): en los INSERT se agrega RETURNING id para poder
    entregar cursor.lastrowid como hace SQLite (la tabla settings no tiene id)."""
    out = sql.replace('%', '%%').replace('?', '%s')
    wants_id = False
    match = re.match(r'\s*INSERT\s+INTO\s+(\w+)', out, re.IGNORECASE)
    if match and match.group(1).lower() != 'settings' and 'RETURNING' not in out.upper():
        out = out.rstrip().rstrip(';') + ' RETURNING id'
        wants_id = True
    return out, wants_id


def _validate_pg(sql):
    """Comprueba con el parser real de PostgreSQL (pglast) que la consulta es válida."""
    import pglast
    counter = iter(range(1, 1000))
    numbered = re.sub(r'%s', lambda _m: f'${next(counter)}', to_pg(sql)[0]).replace('%%', '%')
    pglast.parse_sql(numbered)


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            if 'pytest' in sys.modules and DATABASE_URL != os.environ.get('TEST_DATABASE_URL', ''):
                # Una prueba jamás debe tocar la base real (usuarios, pagos…).
                raise RuntimeError('DATABASE_URL apunta a una base real durante las pruebas; '
                                   'usa TEST_DATABASE_URL para una base de pruebas.')
            from psycopg_pool import ConnectionPool
            _pool = ConnectionPool(
                DATABASE_URL, min_size=1, max_size=POOL_MAX, open=True,
                check=ConnectionPool.check_connection,       # descarta conexiones que el servidor cerró
                kwargs={'row_factory': _row_factory,
                        'autocommit': True,   # cada consulta confirma sola: una generacion dura minutos y no debe dejar una transaccion abierta
                        'prepare_threshold': None},          # el pooler de Supabase no admite prepared statements
            )
        return _pool


def reset_pool():
    """Cierra el pool (cambio de base en pruebas, cierre ordenado)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


class _Cursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cursor, 'lastrowid', None)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _Conn:
    """Misma interfaz mínima sobre sqlite3 y psycopg: execute / commit / close."""

    def __init__(self, raw, postgres, release=None):
        self._raw = raw
        self._pg = postgres
        self._release = release

    def execute(self, sql, params=(), sqlite_only=False):
        """sqlite_only: consulta que solo se emite con SQLite (hay otra para PostgreSQL)."""
        if self._pg:
            pg_sql, wants_id = to_pg(sql)
            cursor = self._raw.execute(pg_sql, tuple(params))
            lastrowid = None
            if wants_id:
                lastrowid = cursor.fetchone()['id']
            return _Cursor(cursor, lastrowid)
        if _VALIDATE_PG and not sqlite_only:
            _validate_pg(sql)
        return _Cursor(self._raw.execute(sql, params))

    def commit(self):
        if not getattr(self, '_in_transaction', False):
            self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        if self._pg:
            try:
                self._raw.rollback()        # lo no confirmado no se queda abierto en el pool
            finally:
                self._release(self._raw)
        else:
            self._raw.close()


def _open():
    if is_postgres():
        pool = _get_pool()
        return _Conn(pool.getconn(), True, pool.putconn)
    raw = sqlite3.connect(DB_PATH)
    raw.row_factory = sqlite3.Row
    raw.execute('PRAGMA foreign_keys = ON')
    return _Conn(raw, False)


@contextmanager
def standalone():
    """Conexión fuera de una petición de Flask (hilo de limpieza, scripts)."""
    conn = _open()
    try:
        yield conn
    finally:
        conn.close()


def get_db():
    if 'db' not in g:
        g.db = _open()
    return g.db


@contextmanager
def transaction():
    conn = get_db()
    conn.execute('BEGIN' if conn._pg else 'BEGIN IMMEDIATE', sqlite_only=not conn._pg)
    conn._in_transaction = True
    try:
        yield conn
        conn._raw.commit()
    except Exception:
        conn._raw.rollback()
        raise
    finally:
        conn._in_transaction = False


def billing_state(user):
    """El cupo mensual se renueva cada 30 días desde la activación."""
    if has_active_plan(user):
        config = plan_catalog()[user['plan']]
        anchor = datetime.strptime(user['plan_started_at'], DATETIME_FMT) if user['plan_started_at'] else plan_expiry(user) - timedelta(days=30)
        cycle = anchor + timedelta(days=max(0, (_utcnow() - anchor).days // 30) * 30)
        stamp = cycle.strftime(DATETIME_FMT)
        used = user['plan_used'] if user['plan_cycle_at'] == stamp else 0
        return {**config, 'source': user['plan'], 'used': used,
                'remaining': max(0, config['limit'] - used), 'cycle': stamp,
                'renews_at': min(cycle + timedelta(days=30), plan_expiry(user))}
    if user['credits'] > 0:
        return {**plan_catalog()['recharge'], 'source': 'recharge', 'used': 0,
                'remaining': user['credits'], 'cycle': None}
    return {'source': 'free', 'hours': get_retention_hours(), 'terms': 100,
            'remaining': None, 'used': 0, 'limit': None}


def reserve_generation(user_id, allow_free=False):
    with transaction() as conn:
        user = conn.execute('SELECT * FROM users WHERE id = ?' + (' FOR UPDATE' if conn._pg else ''), (user_id,)).fetchone()
        state = billing_state(user)
        if user['is_admin']:
            return {**state, 'source': 'admin', 'hours': get_retention_hours(),
                    'terms': max(p['terms'] for p in plan_catalog().values())}
        if state['source'] == 'free':
            return state if allow_free else None
        if state['remaining'] <= 0:
            return None
        if state['source'] == 'recharge':
            conn.execute('UPDATE users SET credits = credits - 1 WHERE id = ?', (user_id,))
        else:
            conn.execute('UPDATE users SET plan_used = ?, plan_cycle_at = ? WHERE id = ?',
                         (state['used'] + 1, state['cycle'], user_id))
        return state


def refund_generation(user_id, ticket):
    if ticket['source'] == 'recharge':
        get_db().execute('UPDATE users SET credits = credits + 1 WHERE id = ?', (user_id,))
    elif ticket['source'] in ('premium', 'pro'):
        get_db().execute('UPDATE users SET plan_used = plan_used - 1 WHERE id = ? '
                         'AND plan = ? AND plan_cycle_at = ? AND plan_used > 0',
                         (user_id, ticket['source'], ticket['cycle']))
    get_db().commit()


def close_db(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def _columns(conn, table):
    if conn._pg:
        rows = conn.execute(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_name = ? AND table_schema = current_schema()', (table,)).fetchall()
        return {row['column_name'] for row in rows}
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})', sqlite_only=True)}


def _migrate(conn):
    """Agrega columnas nuevas a bases de datos creadas antes de los planes."""
    columns = lambda table: _columns(conn, table)      # noqa: E731
    for table, additions in {
        'users': {'auth_version': 'INTEGER NOT NULL DEFAULT 0', 'credits': 'INTEGER NOT NULL DEFAULT 0', 'plan_started_at': 'TEXT',
                  'plan_cycle_at': 'TEXT', 'plan_used': 'INTEGER NOT NULL DEFAULT 0'},
        'payments': {'plan_id': "TEXT NOT NULL DEFAULT 'premium'", 'plan_snapshot': 'TEXT'},
        'documents': {'billing_plan': "TEXT NOT NULL DEFAULT 'free'"},
        'pending_registrations': {'purpose': "TEXT NOT NULL DEFAULT 'register'", 'verified_until': 'INTEGER NOT NULL DEFAULT 0'},
    }.items():
        existing = columns(table)
        for name, definition in additions.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

    if 'plan_expires_at' not in columns('users'):
        conn.execute('ALTER TABLE users ADD COLUMN plan_expires_at TEXT')

    if 'mode' not in columns('documents'):
        # 'ai' = redactado por Gemini, 'manual' = escrito por el usuario. Los
        # documentos anteriores no lo guardaban: los que gastaron tokens
        # fueron con IA, el resto (incluido bachillerato) fue manual.
        conn.execute("ALTER TABLE documents ADD COLUMN mode TEXT NOT NULL DEFAULT 'manual'")
        conn.execute("UPDATE documents SET mode = 'ai' WHERE tokens_used > 0")

    if 'file_stem' not in columns('documents'):
        # Nombre del archivo (sin extensión) y cuándo se borra. Los documentos
        # anteriores no lo guardaban: quedan sin archivo asociado.
        conn.execute('ALTER TABLE documents ADD COLUMN file_stem TEXT')
        conn.execute('ALTER TABLE documents ADD COLUMN expires_at TEXT')


def _schema(postgres):
    if postgres:
        pk = 'SERIAL PRIMARY KEY'
        now = "(to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'))"
    else:
        pk = 'INTEGER PRIMARY KEY AUTOINCREMENT'
        now = "(datetime('now'))"
    return SCHEMA.replace('{PK}', pk).replace('{NOW}', now)


def init_db():
    conn = _open()
    try:
        if conn._pg:
            # Varias instancias arrancando a la vez no deben crear las tablas en paralelo.
            conn.execute('SELECT pg_advisory_lock(727274)')
            try:
                conn._raw.execute(_schema(True))
                _migrate(conn)
                conn.commit()
            finally:
                conn.execute('SELECT pg_advisory_unlock(727274)')
        else:
            conn._raw.executescript(_schema(False))
            _migrate(conn)
            conn.commit()
    finally:
        conn.close()
    _settings_cache.clear()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


def _sync_admin_flag(db, user_id, email):
    """Si el correo está en ADMIN_EMAILS, asegura is_admin=1 para ese usuario."""
    if email.lower().strip() in ADMIN_EMAILS:
        db.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (user_id,))
        db.commit()


def create_user(email, name, password_hash=None, google_id=None):
    email = email.strip().lower()
    db = get_db()
    cur = db.execute(
        'INSERT INTO users (email, name, password_hash, google_id) VALUES (?, ?, ?, ?)',
        (email, name, password_hash, google_id),
    )
    db.commit()
    user_id = cur.lastrowid
    _sync_admin_flag(db, user_id, email)
    return user_id


def get_user_by_email(email):
    db = get_db()
    return db.execute(
        'SELECT * FROM users WHERE email = ?', (email.strip().lower(),)
    ).fetchone()


def pending_registration(token, purpose='register'):
    return get_db().execute('SELECT * FROM pending_registrations WHERE token = ? AND purpose = ?', (token, purpose)).fetchone()


def begin_registration(email, name, password_hash, token, code_hash, now, ip_hash, purpose='register'):
    with transaction() as conn:
        if conn._pg:
            # ponytail: bloqueo global solo durante escrituras breves; usar bloqueos por correo si el volumen lo exige.
            conn.execute('SELECT pg_advisory_xact_lock(727275)')
        conn.execute('DELETE FROM pending_registrations WHERE sent_at < ?', (now - 86400,))
        previous = conn.execute('SELECT * FROM pending_registrations WHERE email = ?', (email,)).fetchone()
        if previous and now - previous['sent_at'] < 60:
            raise ValueError('Espera un minuto antes de solicitar otro código.')
        sends = previous['sends'] if previous and now - previous['window_at'] < 3600 else 0
        if sends >= 5:
            raise ValueError('Alcanzaste el límite de envíos. Inténtalo dentro de una hora.')
        total = conn.execute('SELECT COALESCE(SUM(sends), 0) FROM pending_registrations '
                             'WHERE ip_hash = ? AND window_at > ?', (ip_hash, now - 3600)).fetchone()[0]
        if total >= 20:
            raise ValueError('Demasiadas solicitudes. Inténtalo dentro de una hora.')
        if purpose == 'register' and get_user_by_email(email):
            raise ValueError('Ya existe una cuenta con ese correo. Inicia sesión.')
        window = previous['window_at'] if sends else now
        conn.execute('INSERT INTO pending_registrations '
                     '(email, token, name, password_hash, code_hash, expires_at, sent_at, attempts, sends, window_at, ip_hash, purpose) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?) ON CONFLICT (email) DO UPDATE SET '
                     'token = excluded.token, name = excluded.name, password_hash = excluded.password_hash, '
                     'code_hash = excluded.code_hash, expires_at = excluded.expires_at, sent_at = excluded.sent_at, '
                     'attempts = 0, sends = excluded.sends, window_at = excluded.window_at, ip_hash = excluded.ip_hash, '
                     'purpose = excluded.purpose, verified_until = 0 RETURNING token',
                     (email, token, name, password_hash, code_hash, now + 600, now, sends + 1, window, ip_hash, purpose)).fetchall()


def _check_pending_code(conn, token, code_hash, now, purpose):
    import hmac
    row = conn.execute('SELECT * FROM pending_registrations WHERE token = ? AND purpose = ?' +
                       (' FOR UPDATE' if conn._pg else ''), (token, purpose)).fetchone()
    if not row or not row['code_hash']:
        return None, 'Solicita un nuevo código para continuar.'
    if now >= row['expires_at']:
        return None, 'El código venció. Solicita uno nuevo.'
    if row['attempts'] >= 5:
        return None, 'Alcanzaste el límite de intentos. Solicita un nuevo código.'
    if not hmac.compare_digest(row['code_hash'], code_hash):
        conn.execute('UPDATE pending_registrations SET attempts = attempts + 1 WHERE token = ?', (token,))
        return None, 'Código incorrecto. Revisa el correo e inténtalo de nuevo.'
    return row, None


def complete_registration(token, code_hash, now):
    with transaction() as conn:
        row, error = _check_pending_code(conn, token, code_hash, now, 'register')
        if not row:
            return None, error
        cur = conn.execute('INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?) '
                           'ON CONFLICT (email) DO NOTHING RETURNING id', (row['email'], row['name'], row['password_hash']))
        created = cur.fetchall()
        conn.execute('DELETE FROM pending_registrations WHERE token = ?', (token,))
        if not created:
            return None, 'Ya existe una cuenta con ese correo. Inicia sesión.'
        user_id = created[0][0]
        _sync_admin_flag(conn, user_id, row['email'])
        return user_id, None


def verify_password_reset(token, code_hash, now):
    with transaction() as conn:
        row, error = _check_pending_code(conn, token, code_hash, now, 'reset')
        if not row:
            return error
        conn.execute("UPDATE pending_registrations SET code_hash = '', verified_until = ? WHERE token = ?", (now + 600, token))
        return None


def complete_password_reset(token, password_hash, now):
    with transaction() as conn:
        row = conn.execute("SELECT * FROM pending_registrations WHERE token = ? AND purpose = 'reset'" +
                           (' FOR UPDATE' if conn._pg else ''), (token,)).fetchone()
        if not row or row['verified_until'] <= now:
            return False
        result = conn.execute('UPDATE users SET password_hash = ?, auth_version = auth_version + 1 '
                              'WHERE email = ? AND password_hash IS NOT NULL', (password_hash, row['email']))
        conn.execute('DELETE FROM pending_registrations WHERE token = ?', (token,))
        return result.rowcount == 1


def get_user_by_google_id(google_id):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE google_id = ?', (google_id,)).fetchone()


def get_user_by_id(user_id):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


def link_google_id(user_id, google_id):
    db = get_db()
    db.execute('UPDATE users SET google_id = ? WHERE id = ?', (google_id, user_id))
    db.commit()


def touch_admin_status(user):
    """Vuelve a chequear ADMIN_EMAILS en cada login, por si se agregó el
    correo a la variable de entorno después de que el usuario ya existía."""
    if user is not None and not user['is_admin']:
        _sync_admin_flag(get_db(), user['id'], user['email'])


def record_document(user_id, title, doc_type, tokens_used, mode='manual', file_stem=None):
    """Registra un documento generado. Con `file_stem` (nombre del archivo en
    output/, sin extensión) queda además disponible en "Mis informes" hasta
    que vence el plazo configurado al crearlo."""
    created_at = _utcnow()
    ticket = getattr(g, 'generation_ticket', None)
    expires_at = None
    if file_stem:
        hours = ticket['hours'] if ticket else (plan_catalog()[get_user_by_id(user_id)['plan']]['hours']
            if has_active_plan(get_user_by_id(user_id)) else get_retention_hours())
        expires_at = (created_at + timedelta(hours=hours)).strftime(DATETIME_FMT)
    db = get_db()
    db.execute(
        'INSERT INTO documents (user_id, title, doc_type, tokens_used, mode, file_stem, expires_at, created_at, billing_plan) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (user_id, title, doc_type, tokens_used, mode, file_stem, expires_at, created_at.strftime(DATETIME_FMT),
         ticket['source'] if ticket else 'free'),
    )
    db.commit()
    if ticket:
        ticket['done'] = True


def list_user_documents(user_id, limit=100, offset=0):
    """Informes del usuario cuyos archivos todavía no vencen, el más nuevo primero."""
    return get_db().execute(
        'SELECT * FROM documents WHERE user_id = ? AND file_stem IS NOT NULL '
        'AND expires_at > ? ORDER BY id DESC LIMIT ? OFFSET ?',
        (user_id, _utcnow().strftime(DATETIME_FMT), limit, offset),
    ).fetchall()


def get_user_document(user_id, doc_id):
    """Un informe del usuario (o None si no existe, no es suyo o ya venció)."""
    return get_db().execute(
        'SELECT * FROM documents WHERE id = ? AND user_id = ? AND file_stem IS NOT NULL '
        'AND expires_at > ?',
        (doc_id, user_id, _utcnow().strftime(DATETIME_FMT)),
    ).fetchone()


def format_duration(hours):
    days, hours = divmod(max(0, int(hours)), 24)
    parts = []
    if days:
        parts.append(f'{days} día' + ('s' if days != 1 else ''))
    if hours:
        parts.append(f'{hours} hora' + ('s' if hours != 1 else ''))
    return ' y '.join(parts) or 'menos de una hora'


def get_retention_hours():
    return int(get_settings()['file_retention_hours'])


def get_document_by_filename(filename):
    return get_db().execute(
        'SELECT * FROM documents WHERE file_stem = ? ORDER BY id DESC LIMIT 1',
        (filename,),
    ).fetchone()


def time_left(expires_at, created_at=None):
    """Texto en días y horas y fracción del plazo original de cada documento.
    Las horas se redondean hacia abajo. Debe coincidir con formatTimeLeft() de
    my_documents.html, que lo mantiene al día sin recargar la página."""
    try:
        expiry = datetime.strptime(expires_at, DATETIME_FMT)
    except (TypeError, ValueError):
        return 'vencido', 0.0
    seconds = max(0, int((expiry - _utcnow()).total_seconds()))
    total = FILE_RETENTION_HOURS * 3600
    if created_at:
        total = max(1, (expiry - datetime.strptime(created_at, DATETIME_FMT)).total_seconds())
    text = format_duration(seconds // 3600) if seconds else 'vencido'
    return text, min(1.0, seconds / total)


def count_user_documents(user_id, mode):
    row = get_db().execute(
        'SELECT COUNT(*) AS n FROM documents WHERE user_id = ? AND mode = ?',
        (user_id, mode),
    ).fetchone()
    return row['n']


# ── Ajustes ───────────────────────────────────────────────────────────

SETTINGS_CACHE_SECONDS = 5


class _SettingsCache:
    """Ajustes en memoria unos segundos. Se invalida al guardar y al iniciar la
    base; otras instancias ven un cambio como máximo tras SETTINGS_CACHE_SECONDS."""

    def __init__(self):
        self._lock = threading.Lock()
        self.clear()

    def clear(self):
        with self._lock:
            self._key = None
            self._expires = 0.0
            self._values = None

    def get(self, key):
        with self._lock:
            if self._key == key and time.monotonic() < self._expires:
                return dict(self._values)
        return None

    def put(self, key, values):
        with self._lock:
            self._key, self._values = key, dict(values)
            self._expires = time.monotonic() + SETTINGS_CACHE_SECONDS


_settings_cache = _SettingsCache()


def get_settings():
    """Todos los ajustes: los guardados encima de los valores por defecto."""
    key = DATABASE_URL if is_postgres() else DB_PATH
    cached = _settings_cache.get(key)
    if cached is not None:
        return cached
    values = dict(SETTING_DEFAULTS)
    for row in get_db().execute('SELECT key, value FROM settings'):
        if row['key'] in values:
            values[row['key']] = row['value']
    _settings_cache.put(key, values)
    return values


def set_settings(new_values):
    """Guarda sólo claves conocidas (ignora cualquier otra)."""
    db = get_db()
    for key, value in new_values.items():
        if key in SETTING_DEFAULTS:
            db.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, str(value)),
            )
    db.commit()
    _settings_cache.clear()


# ── Plan de pago ──────────────────────────────────────────────────────

DATETIME_FMT = '%Y-%m-%d %H:%M:%S'


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def plan_expiry(user):
    """datetime de vencimiento del plan, o None si no tiene."""
    raw = user['plan_expires_at']
    if not raw:
        return None
    try:
        return datetime.strptime(raw, DATETIME_FMT)
    except ValueError:
        return None


def has_active_plan(user):
    if user is None or user['plan'] not in ('premium', 'pro'):
        return False
    expiry = plan_expiry(user)
    return expiry is not None and expiry > _utcnow()


def grant_plan(user_id, days, plan_id='premium'):
    """Activa el plan `days` días. Si ya está activo, suma al vencimiento
    actual en vez de perder los días que le quedaban."""
    db = get_db()
    if not getattr(db, '_in_transaction', False):
        with transaction():
            return grant_plan(user_id, days, plan_id)
    user = db.execute('SELECT * FROM users WHERE id = ?' + (' FOR UPDATE' if db._pg else ''), (user_id,)).fetchone()
    start = _utcnow()
    if has_active_plan(user) and user['plan'] == plan_id:
        start = plan_expiry(user)
    expires = (start + timedelta(days=days)).strftime(DATETIME_FMT)
    if has_active_plan(user) and user['plan'] == plan_id:
        db.execute('UPDATE users SET plan_expires_at = ? WHERE id = ?', (expires, user_id))
    else:
        stamp = _utcnow().strftime(DATETIME_FMT)
        db.execute('UPDATE users SET plan = ?, plan_expires_at = ?, plan_started_at = ?, '
                   'plan_cycle_at = ?, plan_used = 0 WHERE id = ?', (plan_id, expires, stamp, stamp, user_id))
    db.commit()
    return expires


def revoke_plan(user_id):
    db = get_db()
    db.execute(
        "UPDATE users SET plan = 'free', plan_expires_at = NULL WHERE id = ?",
        (user_id,),
    )
    db.commit()


# ── Pagos ─────────────────────────────────────────────────────────────

def reference_in_use(method, reference):
    """Una referencia ya reportada (pendiente o aprobada) no se puede
    volver a usar: evita que el mismo comprobante active varias cuentas."""
    row = get_db().execute(
        "SELECT 1 FROM payments WHERE method = ? AND lower(reference) = lower(?) "
        "AND status IN ('pending', 'approved')",
        (method, reference),
    ).fetchone()
    return row is not None


def create_payment(user_id, method, reference, amount_usd, plan_id='premium'):
    db = get_db()
    cur = db.execute(
        'INSERT INTO payments (user_id, method, reference, amount_usd, plan_id, plan_snapshot) VALUES (?, ?, ?, ?, ?, ?)',
        (user_id, method, reference, amount_usd, plan_id, json.dumps(plan_catalog()[plan_id])),
    )
    db.commit()
    return cur.lastrowid


def get_user_payments(user_id, limit=10):
    return get_db().execute(
        'SELECT * FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT ?',
        (user_id, limit),
    ).fetchall()


def list_payments(status=None, limit=100):
    query = (
        'SELECT p.*, u.email AS user_email, u.name AS user_name '
        'FROM payments p JOIN users u ON u.id = p.user_id '
    )
    args = []
    if status:
        query += 'WHERE p.status = ? '
        args.append(status)
    query += 'ORDER BY p.id DESC LIMIT ?'
    args.append(limit)
    return get_db().execute(query, args).fetchall()


def review_payment(payment_id, approve, admin_id, days):
    """Aprueba o rechaza un pago pendiente. Devuelve True si cambió algo.
    Sólo actúa sobre pagos 'pending': aprobar dos veces el mismo pago no
    suma días dos veces."""
    with transaction() as conn:
        payment = conn.execute('SELECT * FROM payments WHERE id = ?' + (' FOR UPDATE' if conn._pg else ''), (payment_id,)).fetchone()
        if payment is None or payment['status'] != 'pending':
            return False
        conn.execute('UPDATE payments SET status = ?, reviewed_at = ?, reviewed_by = ? WHERE id = ?',
            ('approved' if approve else 'rejected', _utcnow().strftime(DATETIME_FMT), admin_id, payment_id))
        if approve:
            if payment['plan_id'] == 'recharge':
                config = json.loads(payment['plan_snapshot']) if payment['plan_snapshot'] else plan_catalog()['recharge']
                conn.execute('UPDATE users SET credits = credits + ? WHERE id = ?', (config['limit'], payment['user_id']))
            else:
                grant_plan(payment['user_id'], days, payment['plan_id'])
        return True


def list_users():
    db = get_db()
    return db.execute("""
        SELECT u.*,
               COUNT(d.id)                  AS documents_count,
               COALESCE(SUM(d.tokens_used), 0) AS tokens_used
        FROM users u
        LEFT JOIN documents d ON d.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """).fetchall()


def list_documents(limit=200):
    db = get_db()
    return db.execute("""
        SELECT d.*, u.email AS user_email, u.name AS user_name
        FROM documents d
        JOIN users u ON u.id = d.user_id
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_stats():
    db = get_db()
    return db.execute("""
        SELECT
            (SELECT COUNT(*) FROM users)                        AS total_users,
            (SELECT COUNT(*) FROM documents)                    AS total_documents,
            (SELECT COALESCE(SUM(tokens_used), 0) FROM documents) AS total_tokens
    """).fetchone()


# Día en hora de Venezuela (UTC-4) de una fecha guardada como texto UTC.
DAY_EXPR_PG = "to_char(created_at::timestamp - interval '4 hours', 'YYYY-MM-DD')"
DAY_EXPR_SQLITE = "date(created_at, '-4 hours')"


def get_dashboard_stats(days=30):
    """Series diarias en hora de Venezuela, incluyendo días sin actividad."""
    today = (_utcnow() - timedelta(hours=4)).date()
    start = today - timedelta(days=days - 1)
    cutoff = datetime.combine(start, datetime.min.time()) + timedelta(hours=4)
    conn = get_db()
    day = DAY_EXPR_PG if is_postgres() else DAY_EXPR_SQLITE
    rows = conn.execute(f"""
        SELECT {day} AS day, COUNT(*) AS documents,
               SUM(CASE WHEN mode = 'ai' THEN 1 ELSE 0 END) AS ai,
               SUM(CASE WHEN mode = 'manual' THEN 1 ELSE 0 END) AS manual,
               COALESCE(SUM(tokens_used), 0) AS tokens
        FROM documents WHERE created_at >= ? AND created_at <= ? GROUP BY {day}
    """, sqlite_only=not is_postgres(), params=(cutoff.strftime(DATETIME_FMT), _utcnow().strftime(DATETIME_FMT))).fetchall()
    by_day = {row['day']: dict(row) for row in rows}
    daily = [by_day.get((start + timedelta(days=i)).isoformat(),
                       {'day': (start + timedelta(days=i)).isoformat(),
                        'documents': 0, 'ai': 0, 'manual': 0, 'tokens': 0}) for i in range(days)]
    totals = dict(get_stats())
    totals.update(dict(conn.execute("""
        SELECT
        (SELECT COUNT(*) FROM users WHERE plan IN ('premium', 'pro') AND plan_expires_at > ?) AS premium,
        (SELECT COUNT(*) FROM payments WHERE status = 'pending') AS pending,
        (SELECT COALESCE(SUM(amount_usd), 0) FROM payments WHERE status = 'approved') AS revenue,
        (SELECT COUNT(*) FROM documents WHERE file_stem IS NOT NULL AND expires_at > ?) AS available,
        (SELECT COUNT(DISTINCT user_id) FROM documents WHERE created_at >= ?) AS active_users,
        (SELECT COUNT(*) FROM users WHERE created_at >= ?) AS new_users
    """, (_utcnow().strftime(DATETIME_FMT), _utcnow().strftime(DATETIME_FMT),
          cutoff.strftime(DATETIME_FMT), cutoff.strftime(DATETIME_FMT))).fetchone()))
    totals['period_documents'] = sum(row['documents'] for row in daily)
    totals['period_tokens'] = sum(row['tokens'] for row in daily)
    totals['period_ai'] = sum(row['ai'] for row in daily)
    totals['period_manual'] = sum(row['manual'] for row in daily)
    totals['average_tokens'] = round(totals['period_tokens'] / totals['period_ai']) if totals['period_ai'] else 0
    return totals, daily


# ── Limpieza de archivos vencidos (la usa retention.py) ───────────────

def expired_documents(now=None):
    """[(id, file_stem)] de los documentos cuyo archivo ya venció."""
    limit = (now or _utcnow()).strftime(DATETIME_FMT)
    with standalone() as conn:
        rows = conn.execute(
            'SELECT id, file_stem FROM documents WHERE file_stem IS NOT NULL AND expires_at <= ?',
            (limit,)).fetchall()
    return [(row['id'], row['file_stem']) for row in rows]


def detach_file(doc_id):
    """El archivo ya se borró: el documento queda en el historial sin archivo."""
    with standalone() as conn:
        conn.execute('UPDATE documents SET file_stem = NULL WHERE id = ?', (doc_id,))
        conn.commit()


def active_file_stems(now=None):
    """Nombres de archivo que siguen vigentes según la base de datos."""
    limit = (now or _utcnow()).strftime(DATETIME_FMT)
    with standalone() as conn:
        rows = conn.execute(
            'SELECT file_stem FROM documents WHERE file_stem IS NOT NULL AND expires_at > ?',
            (limit,)).fetchall()
    return {row['file_stem'] for row in rows}
