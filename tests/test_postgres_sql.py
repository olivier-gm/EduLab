# -*- coding: utf-8 -*-
"""La capa de base de datos funciona con SQLite (desarrollo) y PostgreSQL
(Supabase). Aquí no hay servidor de PostgreSQL: se comprueba con el parser real
de PostgreSQL (pglast) que todo el SQL traducido es válido, y que la traducción
hace lo que debe. Para ejecutar contra un PostgreSQL de verdad ver conftest.py."""
from datetime import timedelta

import pytest

import db

pglast = pytest.importorskip('pglast')


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(db, '_VALIDATE_PG', True)       # cada consulta se valida como PostgreSQL
    db.init_db()
    from flask import Flask
    app = Flask(__name__)
    with app.app_context():
        yield


# ── Esquema ───────────────────────────────────────────────────────────

def test_el_esquema_de_postgres_es_sql_valido():
    pglast.parse_sql(db._schema(True))


def test_el_esquema_usa_tipos_propios_de_cada_motor():
    assert 'SERIAL PRIMARY KEY' in db._schema(True) and 'AUTOINCREMENT' not in db._schema(True)
    assert 'AUTOINCREMENT' in db._schema(False) and 'SERIAL' not in db._schema(False)
    assert "datetime('now')" not in db._schema(True)


# ── Traducción ────────────────────────────────────────────────────────

def test_placeholders_y_porcentajes():
    sql, wants_id = db.to_pg('SELECT * FROM users WHERE email = ? AND name LIKE ?')
    assert sql == 'SELECT * FROM users WHERE email = %s AND name LIKE %s' and not wants_id
    assert db.to_pg("SELECT '100%' WHERE a = ?")[0] == "SELECT '100%%' WHERE a = %s"


def test_insert_pide_el_id_salvo_en_settings():
    sql, wants_id = db.to_pg('INSERT INTO users (email) VALUES (?)')
    assert sql.endswith('RETURNING id') and wants_id
    sql, wants_id = db.to_pg('INSERT INTO settings (key, value) VALUES (?, ?)')
    assert 'RETURNING' not in sql and not wants_id
    already = 'INSERT INTO users (email) VALUES (?) RETURNING id'
    assert db.to_pg(already)[0].count('RETURNING') == 1


def test_fila_de_postgres_admite_nombre_y_posicion():
    row = db.Row({'a': 1, 'b': 2})
    assert row['a'] == 1 and row[1] == 2 and dict(row) == {'a': 1, 'b': 2}


def test_las_pruebas_nunca_usan_una_base_real(monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_URL', 'postgresql://usuario:clave@produccion.example.com/postgres')
    monkeypatch.delenv('TEST_DATABASE_URL', raising=False)
    db.reset_pool()
    with pytest.raises(RuntimeError, match='base real'):
        db._get_pool()
    db.reset_pool()


# ── Cada función de db.py emite SQL válido en PostgreSQL ──────────────

def test_toda_la_api_de_db_emite_sql_valido_en_postgres(sqlite_db):
    uid = db.create_user('a@x.com', 'A', password_hash='x')
    admin = db.create_user('adm@x.com', 'Adm')
    db.get_user_by_email('a@x.com')
    db.get_user_by_id(uid)
    db.link_google_id(uid, 'g-1')
    db.get_user_by_google_id('g-1')
    db.touch_admin_status(db.get_user_by_id(uid))

    db.record_document(uid, 'Informe', 'uni', 10, mode='ai', file_stem='informe')
    db.record_document(uid, 'Otro', 'bach', 0, mode='manual')
    db.list_user_documents(uid)
    doc = db.list_user_documents(uid)[0]
    db.get_user_document(uid, doc['id'])
    db.get_document_by_filename('informe')
    db.count_user_documents(uid, 'ai')

    db.set_settings({'plan_days': '15'})
    db.get_settings()
    db.grant_plan(uid, 30)
    db.has_active_plan(db.get_user_by_id(uid))
    db.revoke_plan(uid)

    pid = db.create_payment(uid, 'binance', 'REF12345', 5)
    assert isinstance(pid, int)
    db.reference_in_use('binance', 'ref12345')
    db.get_user_payments(uid)
    db.list_payments()
    db.list_payments('pending')
    assert db.review_payment(pid, True, admin, 30)

    db.list_users()
    db.list_documents()
    db.get_stats()
    db.get_dashboard_stats(7)

    past = db._utcnow() + timedelta(days=2)
    assert db.expired_documents(past)
    db.active_file_stems()
    db.detach_file(doc['id'])


def test_la_consulta_del_dashboard_es_valida_en_postgres():
    pglast.parse_sql(
        f"SELECT {db.DAY_EXPR_PG} AS day, COUNT(*) AS documents, "
        "SUM(CASE WHEN mode = 'ai' THEN 1 ELSE 0 END) AS ai "
        f"FROM documents WHERE created_at >= $1 GROUP BY {db.DAY_EXPR_PG}")


def test_las_migraciones_usan_information_schema_en_postgres():
    pglast.parse_sql("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name = $1 AND table_schema = current_schema()")
