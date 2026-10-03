# -*- coding: utf-8 -*-
"""Aislamiento de las pruebas.

Las variables de la base de datos y del almacenamiento se vacían ANTES de
importar la app, para que ninguna prueba toque jamás Supabase ni R2 reales
aunque el .env las tenga (load_dotenv no pisa variables ya definidas).

Para correr TODA la suite contra un PostgreSQL de pruebas:

    set TEST_DATABASE_URL=postgresql://usuario:clave@localhost:5432/pruebas
    pytest tests

OJO: en ese modo se BORRA el esquema "public" de esa base antes de cada
prueba; usa una base desechable, nunca la de producción.
"""
import os

import pytest

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL', '').strip()

os.environ['DATABASE_URL'] = TEST_DATABASE_URL
os.environ['BINANCE_VERIFY_TOKEN'] = ''
for _name in ('R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET',
              'R2_PREFIX', 'S3_ENDPOINT_URL'):
    os.environ[_name] = ''
os.environ['RATE_LIMIT_ENABLED'] = '0'


@pytest.fixture(autouse=True)
def _base_limpia_en_postgres():
    """Con TEST_DATABASE_URL, cada prueba empieza con el esquema vacío."""
    if not TEST_DATABASE_URL:
        yield
        return
    import db
    db.reset_pool()
    with db.standalone() as conn:
        conn.execute('DROP SCHEMA public CASCADE')
        conn.execute('CREATE SCHEMA public')
        conn.commit()
    db.init_db()
    yield
    db.reset_pool()


@pytest.fixture(scope='session', autouse=True)
def _validar_sql_postgres_si_se_pide():
    """VALIDATE_PG_SQL=1: cada consulta que ejecute CUALQUIER prueba se valida
    además con el parser real de PostgreSQL (pglast), sin necesitar un servidor."""
    if os.environ.get('VALIDATE_PG_SQL'):
        import db
        db._VALIDATE_PG = True
    yield


@pytest.fixture(autouse=True)
def _sin_correccion_de_texto_con_ia(monkeypatch):
    """Ninguna prueba debe llamar al modelo real para corregir mayúsculas y tildes
    (gastaría tokens y cambiaría los totales). Cae al respaldo; las pruebas de
    text_format simulan el modelo a propósito."""
    import text_format

    def no_model(texts, usage_sink):
        raise RuntimeError('sin IA en las pruebas')
    monkeypatch.setattr(text_format, '_propose', no_model)


@pytest.fixture(autouse=True)
def _planes_de_regresion(request, monkeypatch):
    """Las pruebas previas ejercitan planes públicos; las nuevas prueban ambos modos.
    Nunca usan el token de Binance real del .env.
    """
    import db
    monkeypatch.setenv('BINANCE_VERIFY_TOKEN', '')
    if request.module.__name__ != 'test_binance_payments':
        monkeypatch.setitem(db.SETTING_DEFAULTS, 'plans_public_enabled', '1')
