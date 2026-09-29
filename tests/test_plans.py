# -*- coding: utf-8 -*-
"""Planes de pago: reglas de acceso, pagos y bloqueo de rutas."""
from datetime import timedelta

import pytest

import db
import plans

app_module = pytest.importorskip('app')


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Cliente Flask con una base de datos vacía y aislada."""
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    app_module.app.config['TESTING'] = True
    # Contexto de aplicación abierto durante todo el test: los helpers de
    # db.* (make_user, add_docs, ...) usan flask.g.
    with app_module.app.app_context():
        yield app_module.app.test_client()


@pytest.fixture
def ctx(client):
    yield


def make_user(email='a@x.com', admin=False):
    uid = db.create_user(email, 'Test')
    if admin:
        db.get_db().execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
        db.get_db().commit()
    return uid


def user(uid):
    return db.get_user_by_id(uid)


def login(client, uid):
    with client.session_transaction() as sess:
        sess['user_id'] = uid


def add_docs(uid, mode, n):
    for _ in range(n):
        db.record_document(uid, 'T', 'uni', 0, mode=mode)


# ── Reglas de acceso ──────────────────────────────────────────────────

def test_por_defecto_sin_plan_puede_generar(ctx):
    uid = make_user()
    assert plans.generation_access(user(uid), 'ai') == (True, None)
    assert plans.generation_access(user(uid), 'manual') == (True, None)


def test_modo_desactivado_se_bloquea(ctx):
    uid = make_user()
    db.set_settings({'free_ai_enabled': '0'})
    assert plans.generation_access(user(uid), 'ai') == (False, 'ai_disabled')
    assert plans.generation_access(user(uid), 'manual')[0] is True


def test_limite_por_modo_se_cuenta_por_separado(ctx):
    uid = make_user()
    db.set_settings({'free_ai_limit': '2', 'free_manual_limit': '1'})
    add_docs(uid, 'ai', 2)
    add_docs(uid, 'manual', 1)
    assert plans.generation_access(user(uid), 'ai') == (False, 'ai_limit')
    assert plans.generation_access(user(uid), 'manual') == (False, 'manual_limit')


def test_limite_no_afecta_a_otros_usuarios(ctx):
    a, b = make_user('a@x.com'), make_user('b@x.com')
    db.set_settings({'free_ai_limit': '1'})
    add_docs(a, 'ai', 1)
    assert plans.generation_access(user(a), 'ai')[0] is False
    assert plans.generation_access(user(b), 'ai')[0] is True


def test_limite_cero_bloquea(ctx):
    uid = make_user()
    db.set_settings({'free_manual_limit': '0'})
    assert plans.generation_access(user(uid), 'manual') == (False, 'manual_limit')


def test_plan_activo_ignora_limites(ctx):
    uid = make_user()
    db.set_settings({'free_ai_enabled': '0', 'free_manual_limit': '0'})
    db.grant_plan(uid, 30)
    assert plans.generation_access(user(uid), 'ai')[0] is True
    assert plans.generation_access(user(uid), 'manual')[0] is True


def test_plan_vencido_vuelve_a_las_reglas_gratuitas(ctx):
    uid = make_user()
    db.set_settings({'free_ai_enabled': '0'})
    db.grant_plan(uid, 30)
    db.get_db().execute(
        "UPDATE users SET plan_expires_at = ? WHERE id = ?",
        ((db._utcnow() - timedelta(days=1)).strftime(db.DATETIME_FMT), uid))
    db.get_db().commit()
    assert not db.has_active_plan(user(uid))
    assert plans.generation_access(user(uid), 'ai') == (False, 'ai_disabled')


def test_admin_no_tiene_limites(ctx):
    uid = make_user(admin=True)
    db.set_settings({'free_ai_enabled': '0'})
    assert plans.generation_access(user(uid), 'ai')[0] is True


# ── Plan y pagos ──────────────────────────────────────────────────────

def test_extender_suma_a_los_dias_restantes(ctx):
    uid = make_user()
    db.grant_plan(uid, 30)
    first = db.plan_expiry(user(uid))
    db.grant_plan(uid, 30)
    assert db.plan_expiry(user(uid)) - first == timedelta(days=30)


def test_aprobar_pago_activa_plan_y_no_se_aplica_dos_veces(ctx):
    uid, admin = make_user(), make_user('adm@x.com', admin=True)
    pid = db.create_payment(uid, 'binance', 'REF12345', 5)
    assert db.review_payment(pid, True, admin, 30) is True
    assert db.has_active_plan(user(uid))
    expiry = db.plan_expiry(user(uid))
    assert db.review_payment(pid, True, admin, 30) is False
    assert db.plan_expiry(user(uid)) == expiry


def test_rechazar_pago_no_activa_plan(ctx):
    uid, admin = make_user(), make_user('adm@x.com', admin=True)
    pid = db.create_payment(uid, 'pago_movil', 'REF12345', 5)
    db.review_payment(pid, False, admin, 30)
    assert not db.has_active_plan(user(uid))


def test_referencia_repetida_se_detecta(ctx):
    uid = make_user()
    db.create_payment(uid, 'binance', 'ABCD1234', 5)
    assert db.reference_in_use('binance', 'abcd1234')
    assert not db.reference_in_use('pago_movil', 'abcd1234')


def test_ajustes_ignoran_claves_desconocidas(ctx):
    db.set_settings({'plan_days': '15', 'no_existe': 'x'})
    values = db.get_settings()
    assert values['plan_days'] == '15'
    assert 'no_existe' not in values


# ── Rutas ─────────────────────────────────────────────────────────────

def test_formulario_redirige_a_planes_si_nada_esta_permitido(client):
    uid = make_user()
    db.set_settings({'free_ai_enabled': '0', 'free_manual_enabled': '0'})
    login(client, uid)
    resp = client.get('/form')
    assert resp.status_code == 302 and '/plans' in resp.headers['Location']


def test_generar_sin_permiso_redirige_a_planes_sin_llamar_a_la_ia(client, monkeypatch):
    uid = make_user()
    db.set_settings({'free_ai_enabled': '0'})
    login(client, uid)

    def boom(*a, **k):
        raise AssertionError('no debe llamarse a Gemini sin permiso')
    monkeypatch.setattr(app_module, 'validate_titles', boom)

    resp = client.post('/process_form', data={'title': 'Un titulo', 'global-mode': 'ia'})
    assert resp.status_code == 302
    assert '/plans' in resp.headers['Location'] and 'ai_disabled' in resp.headers['Location']


def test_pagina_de_planes_muestra_el_motivo(client):
    uid = make_user()
    login(client, uid)
    html = client.get('/plans?reason=ai_disabled').get_data(as_text=True)
    assert 'disponibles solo con el plan' in html


def test_reportar_pago_valida_y_evita_duplicados(client):
    uid = make_user()
    login(client, uid)
    bad = client.post('/plans/pay', data={'method': 'binance', 'reference': 'x'})
    assert 'error=' in bad.headers['Location']
    ok = client.post('/plans/pay', data={'method': 'binance', 'reference': 'REF12345'})
    assert 'sent=1' in ok.headers['Location']
    again = client.post('/plans/pay', data={'method': 'binance', 'reference': 'OTRA9999'})
    assert 'error=' in again.headers['Location']  # ya tiene uno pendiente


def test_admin_routes_requieren_admin(client):
    uid = make_user()
    login(client, uid)
    assert client.post('/admin/settings', data={}).status_code == 302
    assert client.post('/admin/payments/1/approve').status_code == 302
    assert not db.has_active_plan(user(uid))


def test_admin_guarda_ajustes_y_aprueba(client):
    admin = make_user('adm@x.com', admin=True)
    uid = make_user()
    pid = None
    with app_module.app.test_request_context('/'):
        pid = db.create_payment(uid, 'binance', 'REF12345', 5)
    login(client, admin)

    client.post('/admin/settings', data={
        'free_ai_limit': '3', 'free_manual_enabled': 'on', 'free_manual_limit': '',
        'plan_price_usd': '5', 'plan_days': '30', 'binance_email': 'pay@x.com',
    })
    client.post(f'/admin/payments/{pid}/approve')

    with app_module.app.test_request_context('/'):
        s = db.get_settings()
        assert s['free_ai_enabled'] == '0'      # checkbox sin marcar
        assert s['free_ai_limit'] == '3'
        assert s['free_manual_enabled'] == '1'
        assert s['binance_email'] == 'pay@x.com'
        assert db.has_active_plan(user(uid))


def test_admin_rechaza_limite_invalido(client):
    admin = make_user('adm@x.com', admin=True)
    login(client, admin)
    resp = client.post('/admin/settings', data={
        'free_ai_limit': 'abc', 'plan_price_usd': '5', 'plan_days': '30'})
    assert 'msg=' in resp.headers['Location']
    with app_module.app.test_request_context('/'):
        assert db.get_settings()['free_ai_limit'] == ''
