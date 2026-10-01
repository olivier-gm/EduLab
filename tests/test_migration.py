# -*- coding: utf-8 -*-
"""Migración de datos de SQLite a la base de destino (PostgreSQL en producción)."""
import importlib.util
import os

import pytest

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    'migrate_sqlite_to_postgres', os.path.join(ROOT, 'tools', 'migrate_sqlite_to_postgres.py'))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """(origen con datos, destino vacío): dos bases SQLite con el esquema de la app."""
    from flask import Flask
    source, destination = str(tmp_path / 'origen.db'), str(tmp_path / 'destino.db')

    monkeypatch.setattr(db, 'DB_PATH', source)
    db.init_db()
    with Flask(__name__).app_context():
        admin = db.create_user('adm@x.com', 'Adm')
        user = db.create_user('u@x.com', 'U', password_hash='hash')
        db.record_document(user, 'Informe', 'uni', 120, mode='ai', file_stem='informe')
        pid = db.create_payment(user, 'binance', 'REF12345', 5)
        db.review_payment(pid, True, admin, 30)
        db.set_settings({'plan_days': '15', 'binance_email': 'pagos@x.com'})

    monkeypatch.setattr(db, 'DB_PATH', destination)
    db.init_db()
    return source, destination


def run(source, force=False):
    with db.standalone() as dest:
        return migration.migrate(source, dest, force=force)


def test_copia_todo_y_conserva_los_ids(paths, monkeypatch):
    source, destination = paths
    assert run(source) == {'users': 2, 'documents': 1, 'payments': 1, 'settings': 2}

    from flask import Flask
    with Flask(__name__).app_context():
        assert db.get_user_by_email('u@x.com')['id'] == 2
        assert db.get_user_by_email('u@x.com')['plan'] == 'premium'      # la aprobación del pago
        doc = db.get_document_by_filename('informe')
        assert doc['user_id'] == 2 and doc['tokens_used'] == 120 and doc['mode'] == 'ai'
        assert db.list_payments()[0]['status'] == 'approved'
        db._settings_cache.clear()
        assert db.get_settings()['plan_days'] == '15'


def test_los_registros_nuevos_siguen_despues_de_los_migrados(paths):
    source, _ = paths
    run(source)
    from flask import Flask
    with Flask(__name__).app_context():
        assert db.create_user('nuevo@x.com', 'Nuevo') == 3


def test_se_niega_si_el_destino_ya_tiene_usuarios(paths):
    source, _ = paths
    run(source)
    with pytest.raises(SystemExit, match='ya tiene 2 usuario'):
        run(source)


def test_force_reemplaza_sin_duplicar(paths):
    source, _ = paths
    run(source)
    assert run(source, force=True)['users'] == 2
    from flask import Flask
    with Flask(__name__).app_context():
        assert len(db.list_users()) == 2


def test_las_sentencias_de_postgres_de_la_migracion_son_validas():
    pglast = pytest.importorskip('pglast')
    for table in migration.SERIAL_TABLES:
        sql = (f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
               f'GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1), '
               f'(SELECT COUNT(*) > 0 FROM {table}))')
        pglast.parse_sql(sql)
