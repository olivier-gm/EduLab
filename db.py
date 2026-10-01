# db.py
"""Acceso a la base de datos SQLite: usuarios y documentos generados.

Se usa sqlite3 directo (sin ORM) para mantener la misma filosofía del resto
del proyecto: módulos simples, sin dependencias nuevas que no hagan falta.
La conexión vive en el contexto de la petición de Flask (flask.g), igual
que recomienda la documentación de Flask para sqlite3.
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

from flask import g

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('DATABASE_PATH', 'gullieth.db')

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
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    google_id     TEXT UNIQUE,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    plan          TEXT NOT NULL DEFAULT 'free',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    title        TEXT NOT NULL,
    doc_type     TEXT NOT NULL,
    tokens_used  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);

-- Ajustes editables desde el panel admin (clave/valor).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Pagos del plan: el usuario reporta su referencia y el admin lo aprueba.
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    method      TEXT NOT NULL,            -- 'binance' | 'pago_movil'
    reference   TEXT NOT NULL,
    amount_usd  REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at TEXT,
    reviewed_by INTEGER REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
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
    'gemini_model': '',  # Vacío conserva el modelo del entorno actual.
    'openrouter_model': 'google/gemini-3.8-flash',
    'gemini_api_key': '',  # Cifradas; nunca se rellenan en el HTML.
    'openrouter_api_key': '',
}


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def _migrate(conn):
    """Agrega columnas nuevas a bases de datos creadas antes de los planes."""
    def columns(table):
        return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}

    if 'plan_expires_at' not in columns('users'):
        conn.execute('ALTER TABLE users ADD COLUMN plan_expires_at TEXT')

    if 'mode' not in columns('documents'):
        # 'ai' = redactado por Gemini, 'manual' = escrito por el usuario. Los
        # documentos anteriores no lo guardaban: los que gastaron tokens
        # fueron con IA, el resto (incluido bachillerato) fue manual.
        conn.execute("ALTER TABLE documents ADD COLUMN mode TEXT NOT NULL DEFAULT 'manual'")
        conn.execute("UPDATE documents SET mode = 'ai' WHERE tokens_used > 0")

    if 'file_stem' not in columns('documents'):
        # Nombre del archivo en output/ (sin extensión) y cuándo se borra. Los
        # documentos anteriores no lo guardaban: quedan sin archivo asociado.
        conn.execute('ALTER TABLE documents ADD COLUMN file_stem TEXT')
        conn.execute('ALTER TABLE documents ADD COLUMN expires_at TEXT')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


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
    expires_at = None
    if file_stem:
        expires_at = (created_at + timedelta(hours=get_retention_hours())).strftime(DATETIME_FMT)
    db = get_db()
    db.execute(
        'INSERT INTO documents (user_id, title, doc_type, tokens_used, mode, file_stem, expires_at, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (user_id, title, doc_type, tokens_used, mode, file_stem, expires_at, created_at.strftime(DATETIME_FMT)),
    )
    db.commit()


def list_user_documents(user_id, limit=100):
    """Informes del usuario cuyos archivos todavía no vencen, el más nuevo primero."""
    return get_db().execute(
        'SELECT * FROM documents WHERE user_id = ? AND file_stem IS NOT NULL '
        'AND expires_at > ? ORDER BY id DESC LIMIT ?',
        (user_id, _utcnow().strftime(DATETIME_FMT), limit),
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

def get_settings():
    """Todos los ajustes: los guardados encima de los valores por defecto."""
    values = dict(SETTING_DEFAULTS)
    for row in get_db().execute('SELECT key, value FROM settings'):
        if row['key'] in values:
            values[row['key']] = row['value']
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
    if user is None or user['plan'] != 'premium':
        return False
    expiry = plan_expiry(user)
    return expiry is not None and expiry > _utcnow()


def grant_plan(user_id, days):
    """Activa el plan `days` días. Si ya está activo, suma al vencimiento
    actual en vez de perder los días que le quedaban."""
    user = get_user_by_id(user_id)
    start = _utcnow()
    if has_active_plan(user):
        start = plan_expiry(user)
    expires = (start + timedelta(days=days)).strftime(DATETIME_FMT)
    db = get_db()
    db.execute(
        "UPDATE users SET plan = 'premium', plan_expires_at = ? WHERE id = ?",
        (expires, user_id),
    )
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


def create_payment(user_id, method, reference, amount_usd):
    db = get_db()
    cur = db.execute(
        'INSERT INTO payments (user_id, method, reference, amount_usd) VALUES (?, ?, ?, ?)',
        (user_id, method, reference, amount_usd),
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
    db = get_db()
    payment = db.execute('SELECT * FROM payments WHERE id = ?', (payment_id,)).fetchone()
    if payment is None or payment['status'] != 'pending':
        return False
    db.execute(
        "UPDATE payments SET status = ?, reviewed_at = datetime('now'), reviewed_by = ? WHERE id = ?",
        ('approved' if approve else 'rejected', admin_id, payment_id),
    )
    db.commit()
    if approve:
        grant_plan(payment['user_id'], days)
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
        ORDER BY d.created_at DESC
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


def get_dashboard_stats(days=30):
    """Series diarias en hora de Venezuela, incluyendo días sin actividad."""
    today = (_utcnow() - timedelta(hours=4)).date()
    start = today - timedelta(days=days - 1)
    cutoff = datetime.combine(start, datetime.min.time()) + timedelta(hours=4)
    conn = get_db()
    rows = conn.execute("""
        SELECT date(created_at, '-4 hours') AS day, COUNT(*) AS documents,
               SUM(mode = 'ai') AS ai, SUM(mode = 'manual') AS manual,
               COALESCE(SUM(tokens_used), 0) AS tokens
        FROM documents WHERE created_at >= ? AND created_at <= ? GROUP BY day
    """, (cutoff.strftime(DATETIME_FMT), _utcnow().strftime(DATETIME_FMT))).fetchall()
    by_day = {row['day']: dict(row) for row in rows}
    daily = [by_day.get((start + timedelta(days=i)).isoformat(),
                       {'day': (start + timedelta(days=i)).isoformat(),
                        'documents': 0, 'ai': 0, 'manual': 0, 'tokens': 0}) for i in range(days)]
    totals = dict(get_stats())
    totals.update(dict(conn.execute("""
        SELECT
        (SELECT COUNT(*) FROM users WHERE plan = 'premium' AND plan_expires_at > ?) AS premium,
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
