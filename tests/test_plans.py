# -*- coding: utf-8 -*-
"""Planes de pago: reglas de acceso, pagos y bloqueo de rutas."""
from datetime import timedelta

import pytest

import db
import plans
from decimal import Decimal
from datetime import datetime, timezone
from requests import RequestException

app_module = pytest.importorskip('app')


def test_dolarapi_cache_and_invalid_data(monkeypatch):
    today = datetime.now(timezone(timedelta(hours=-4))).date()
    calls = []

    data = {'moneda': 'USD', 'fuente': 'oficial', 'promedio': 50.123,
            'fechaActualizacion': f'{today}T00:00:00-04:00'}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return data

    def get(url, timeout):
        assert url == 'https://ve.dolarapi.com/v1/dolares/oficial'
        assert timeout == 5
        calls.append(1)
        return Response()

    monkeypatch.setattr(plans.requests, 'get', get)
    plans._bcv_rate.cache_clear()
    try:
        assert plans._bcv_rate(1) == (Decimal('50.123'), today)
        assert plans._bcv_rate(1) == (Decimal('50.123'), today)
        assert len(calls) == 1
        data['promedio'] = 'nan'
        assert plans._bcv_rate(2) is None
        data.update(promedio=50, fechaActualizacion='2020-01-01T00:00:00-04:00')
        assert plans._bcv_rate(3) is None

        data.update(fechaActualizacion=f'{today}T00:00:00-04:00', fuente='paralelo')
        assert plans._bcv_rate(4) is None
        data.clear()
        assert plans._bcv_rate(5) is None

        def unavailable(*args, **kwargs):
            raise RequestException('BCV no disponible')

        monkeypatch.setattr(plans.requests, 'get', unavailable)
        assert plans._bcv_rate(6) is None
    finally:
        plans._bcv_rate.cache_clear()


def test_bcv_amount_on_payment_page(client, monkeypatch):
    login(client, make_user())
    db.set_settings({'pm_phone': '04120000000', 'bs_rate': '40',
                     'binance_email': '12345678', 'pm_id': 'V1234567',
                     'pm_holder': 'Titular', 'pm_bank': 'Banco de Venezuela (BDV) · 0102'})
    today = datetime.now(timezone(timedelta(hours=-4))).date()
    monkeypatch.setattr(plans, '_bcv_rate', lambda bucket: (Decimal('50.123'), today))
    page = client.get('/plans').get_data(as_text=True)
    assert page.count('Bs. 250.11') == 1
    assert 'Tasa BCV:' not in page
    assert '<dt>Tasa</dt>' not in page and 'Cédula/RIF' not in page
    assert 'Banco de Venezuela (BDV)' not in page and 'BDV · 0102' in page
    assert '<dt>ID de Binance</dt>' in page
    assert page.count('class="pay-copy"') == 7
    assert 'data-copy="250.11"' in page and 'data-copy="12345678"' in page
    monkeypatch.setattr(plans, '_bcv_rate', lambda bucket: None)
    page = client.get('/plans').get_data(as_text=True)
    assert 'Bs. 199.60' in page and 'Tasa manual de respaldo:' not in page
    db.set_settings({'bs_rate': ''})
    page = client.get('/plans').get_data(as_text=True)
    assert 'Monto en bolívares pendiente de confirmar' in page


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Cliente Flask con una base de datos vacía y aislada."""
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(plans, '_bcv_rate', lambda bucket: None)
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


def report_payment(client, data):
    client.get('/plans')
    with client.session_transaction() as sess:
        token = sess['payment_csrf_token']
    return client.post('/plans/pay', data={**data, 'csrf_token': token})


def test_pro_terms_migration_runs_once(client):
    conn = db.get_db()
    conn.execute("DELETE FROM settings WHERE key = 'migration_pro_terms_200'")
    conn.commit()
    db.set_settings({'pro_terms': '300'})
    db.init_db()
    assert db.get_settings()['pro_terms'] == '200'
    db.set_settings({'pro_terms': '250'})
    db.init_db()
    assert db.get_settings()['pro_terms'] == '250'


def test_three_plans_recharge_pauses_and_monthly_quota_rolls_over(client, monkeypatch):
    clock = datetime(2026, 10, 1, 12)
    monkeypatch.setattr(db, '_utcnow', lambda: clock)
    uid = make_user()
    catalog = db.plan_catalog()
    assert [(p['price'], p['limit'], p['terms'], p['hours']) for p in catalog.values()] == [
        ('2.99', 20, 100, 1), ('4.99', 400, 100, 72), ('14.99', 2000, 200, 8760)]
    payment = db.create_payment(uid, 'binance', 'REC001', 2.99, 'recharge')
    db.set_settings({'recharge_limit': '25'})
    assert db.review_payment(payment, True, uid, 30)
    assert not db.review_payment(payment, True, uid, 30)
    assert user(uid)['credits'] == 20  # conserva lo adquirido, no el nuevo precio/cupo
    ticket = db.reserve_generation(uid)
    assert ticket['source'] == 'recharge' and ticket['hours'] == 1
    assert user(uid)['credits'] == 19
    db.refund_generation(uid, ticket)
    assert user(uid)['credits'] == 20
    db.grant_plan(uid, 60)
    db.set_settings({'premium_limit': '2'})
    assert db.reserve_generation(uid)['source'] == 'premium'
    assert db.reserve_generation(uid)['source'] == 'premium'
    assert db.reserve_generation(uid) is None
    assert user(uid)['credits'] == 20
    assert plans.generation_access(user(uid), 'ai') == (False, 'monthly_limit')
    clock += timedelta(days=30)
    assert db.billing_state(user(uid))['remaining'] == 2
    assert db.reserve_generation(uid)['source'] == 'premium'
    clock += timedelta(days=31)
    assert db.reserve_generation(uid)['source'] == 'recharge'
    assert user(uid)['credits'] == 19


def test_plan_catalog_payment_selection_disable_and_admin_validation(client):
    uid = make_user(admin=True)
    login(client, uid)
    html = client.get('/plans?plan=pro').get_data(as_text=True)
    assert '$2.99' in html and '$4.99' in html and '$14.99' in html
    assert 'name="plan_id" value="pro"' in html and '200 términos' in html
    db.set_settings({'pro_enabled': '0'})
    assert 'name="plan_id" value="pro"' not in client.get('/plans?plan=pro').get_data(as_text=True)
    report_payment(client, {'method': 'binance', 'reference': 'DISABLED', 'plan_id': 'pro'})
    assert not db.get_user_payments(uid)
    report_payment(client, {'method': 'binance', 'reference': 'RECARGA', 'plan_id': 'recharge', 'amount_usd': '.01'})
    payment = db.get_user_payments(uid)[0]
    assert payment['plan_id'] == 'recharge' and payment['amount_usd'] == 2.99
    client.get('/admin/')
    with client.session_transaction() as sess:
        token = sess['ai_csrf_token']
    form = {'csrf_token': token}
    for key, plan in db.plan_catalog().items():
        form.update({f'{key}_{field}': str(plan[field]) for field in ('name', 'price', 'limit', 'terms', 'hours')})
        form[f'{key}_benefits'] = 'Beneficio nuevo\nWord y PDF'
        form[f'{key}_enabled'] = 'on'
    form['pro_terms'] = '450'
    assert client.post('/admin/catalog-settings', data=form).status_code == 302
    assert db.plan_catalog()['pro']['terms'] == 450
    form['premium_hours'] = '0'
    client.post('/admin/catalog-settings', data=form)
    assert db.plan_catalog()['premium']['hours'] == 72
    form['premium_hours'] = '24'
    form['csrf_token'] = 'incorrecto'
    client.post('/admin/catalog-settings', data=form)
    assert db.plan_catalog()['premium']['hours'] == 72


def test_paid_quota_is_atomic_for_two_simultaneous_requests(client):
    from concurrent.futures import ThreadPoolExecutor
    uid = make_user()
    db.get_db().execute('UPDATE users SET credits = 1 WHERE id = ?', (uid,))
    db.get_db().commit()
    def reserve():
        with app_module.app.app_context():
            return db.reserve_generation(uid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tickets = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(ticket is not None for ticket in tickets) == 1
    assert user(uid)['credits'] == 0


def add_docs(uid, mode, n):
    for _ in range(n):
        db.record_document(uid, 'T', 'uni', 0, mode=mode)


def test_free_mode_shared_limit_reservation_refund_and_notice(client):
    db.set_settings({'plans_public_enabled': '0'})
    uid = make_user()
    add_docs(uid, 'ai', 2)
    add_docs(uid, 'manual', 2)
    assert plans.access_summary(user(uid))['ai']['used'] == 4
    ticket = db.reserve_generation(uid, allow_free=True)
    assert ticket is not None
    assert db.reserve_generation(uid, allow_free=True) is None
    db.refund_generation(uid, ticket)
    assert plans.generation_access(user(uid), 'manual') == (True, None)
    from flask import g
    g.generation_ticket = db.reserve_generation(uid, allow_free=True)
    db.record_document(uid, 'Glosario', 'uni', 0, mode='ai')
    assert g.generation_ticket['done']
    g.pop('generation_ticket')
    assert user(uid)['free_pending'] == 0
    for mode in ('ai', 'manual'):
        assert plans.generation_access(user(uid), mode) == (False, 'free_mode_limit')
    login(client, uid)
    response = client.get('/form')
    assert response.status_code == 200
    assert 'Próximamente estarán disponibles los planes.' in response.get_data(as_text=True)
    assert client.post('/process_form', data={'global-mode': 'standard'}).status_code == 302
    admin = make_user('admin@x.com', admin=True)
    add_docs(admin, 'ai', 5)
    assert plans.generation_access(user(admin), 'ai') == (True, None)


def test_free_mode_last_generation_is_atomic(client):
    from concurrent.futures import ThreadPoolExecutor
    db.set_settings({'plans_public_enabled': '0'})
    uid = make_user()
    add_docs(uid, 'ai', 4)

    def reserve(_):
        with app_module.app.app_context():
            return db.reserve_generation(uid, allow_free=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tickets = list(pool.map(reserve, range(2)))
    assert sum(ticket is not None for ticket in tickets) == 1


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
    monkeypatch.setattr(app_module, 'check_title', boom)

    resp = client.post('/process_form', data={'title': 'Un titulo', 'global-mode': 'ia'})
    assert resp.status_code == 302
    assert '/plans' in resp.headers['Location'] and 'ai_disabled' in resp.headers['Location']


def test_pagina_de_planes_muestra_el_motivo(client):
    uid = make_user()
    login(client, uid)
    html = client.get('/plans?reason=ai_disabled').get_data(as_text=True)
    assert 'disponibles solo con el plan' in html


def test_logos_de_pago_son_opcionales_y_prefieren_svg(client, tmp_path, monkeypatch):
    login(client, make_user())
    monkeypatch.setattr(app_module.app, 'static_folder', str(tmp_path))
    logos = tmp_path / 'img' / 'payments'
    logos.mkdir(parents=True)

    html = client.get('/plans').get_data(as_text=True)
    assert 'pay-logo' not in html
    assert 'Binance' in html and 'Pago Móvil' in html

    for name in ('binance', 'bdv'):
        (logos / f'{name}.webp').write_bytes(b'')
    html = client.get('/plans').get_data(as_text=True)
    assert '/static/img/payments/binance.webp' in html
    assert '/static/img/payments/bdv.webp' in html

    for name in ('binance', 'bdv'):
        (logos / f'{name}.png').write_bytes(b'')
    html = client.get('/plans').get_data(as_text=True)
    assert '/static/img/payments/binance.png' in html
    assert '/static/img/payments/bdv.png' in html
    assert '.webp' not in html

    (logos / 'binance.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    html = client.get('/plans').get_data(as_text=True)
    assert '/static/img/payments/binance.svg' in html
    assert '/static/img/payments/binance.png' not in html
    assert '/static/img/payments/bdv.png' in html


def test_reportar_pago_valida_y_evita_duplicados(client):
    uid = make_user()
    login(client, uid)
    bad = report_payment(client, {'method': 'binance', 'reference': 'x'})
    assert 'error=' in bad.headers['Location']
    ok = report_payment(client, {'method': 'binance', 'reference': 'REF12345'})
    assert 'sent=1' in ok.headers['Location']
    again = report_payment(client, {'method': 'binance', 'reference': 'OTRA9999'})
    assert 'error=' in again.headers['Location']  # ya tiene uno pendiente


def test_admin_routes_requieren_admin(client):
    uid = make_user()
    login(client, uid)
    assert client.post('/admin/settings', data={}).status_code == 302
    assert client.post('/admin/ai-settings', data={}).status_code == 302
    assert client.post('/admin/payments/1/approve').status_code == 302
    assert not db.has_active_plan(user(uid))


def test_admin_shows_persistent_bibliography_origin(client, monkeypatch):
    import glossary
    import IA
    login(client, make_user(admin=True))
    page = client.get('/admin/').get_data(as_text=True)
    assert 'Todavía no se ha generado una bibliografía automática' in page
    monkeypatch.setattr(IA, '_search_blocked_until', 0)
    monkeypatch.setattr(IA, 'SEARCH_ENABLED', False)
    monkeypatch.setattr(glossary, '_research_sources', lambda *a: (
        [{'title': 'Fuente encontrada', 'url': 'https://example.org/fuente'}], 'Investigación'))
    monkeypatch.setattr(glossary, '_json_generate', lambda *a: pytest.fail('Google Search funcionó'))
    assert 'example.org' in glossary.generate_bibliography('Biología', 'Células')
    assert db.get_settings()['bibliography_source'] == 'google_search'
    page = client.get('/admin/').get_data(as_text=True)
    assert 'badge--approved">Google Search' in page and '(hora de Venezuela)' in page
    def unavailable(*args):
        raise IA.GenerationError('unavailable', 'Sin respuesta <script>')
    monkeypatch.setattr(glossary, '_research_sources', unavailable)
    monkeypatch.setattr(glossary, '_json_generate', lambda *a: ['OpenStax. Biology 2e.'])
    assert glossary.generate_bibliography('Biología', 'Células') == 'OpenStax. Biology 2e.'
    db.close_db()  # El estado sobrevive al cierre de la conexión.
    assert db.get_settings()['bibliography_source'] == 'ai'
    page = client.get('/admin/').get_data(as_text=True)
    assert 'IA · sin verificación en internet' in page
    assert 'Sin respuesta &lt;script&gt;' in page


def test_admin_can_switch_providers_without_exposing_keys(client, monkeypatch, tmp_path):
    import ai_provider
    import IA
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path / 'instance'))
    login(client, make_user(admin=True))
    page = client.get('/admin/').get_data(as_text=True)
    assert 'Proveedor y modelo de IA' in page
    with client.session_transaction() as sess:
        csrf = sess['ai_csrf_token']
    data = {'ai_provider': 'openrouter', 'gemini_model': 'gemini-3.8-flash',
            'openrouter_model': 'google/gemini-3.8-flash', 'openrouter_api_key': 'test-admin-secret'}
    client.post('/admin/ai-settings', data=data)
    assert db.get_settings()['ai_provider'] == 'gemini'  # Token obligatorio.
    IA._search_blocked_until = 1000
    data['csrf_token'] = csrf
    client.post('/admin/ai-settings', data=data)
    values = db.get_settings()
    assert values['ai_provider'] == 'openrouter' and IA._search_blocked_until == 0
    assert values['openrouter_api_key'] and 'test-admin-secret' not in values['openrouter_api_key']
    assert ai_provider.configuration(values=values) == ('openrouter', 'google/gemini-3.8-flash', 'test-admin-secret')
    assert (tmp_path / 'instance' / 'ai-secret.key').is_file()
    page = client.get('/admin/').get_data(as_text=True)
    assert 'test-admin-secret' not in page and values['openrouter_api_key'] not in page
    data['openrouter_api_key'] = ''
    data['openrouter_model'] = 'google/gemini-3.7-flash'
    client.post('/admin/ai-settings', data=data)
    assert db.get_settings()['openrouter_api_key'] == values['openrouter_api_key']
    data['openrouter_model'] = 'not-a-gemini-model'
    client.post('/admin/ai-settings', data=data)
    assert db.get_settings()['openrouter_model'] == 'google/gemini-3.7-flash'
    monkeypatch.setenv('GEMINI_API_KEY', 'test-google-env')
    data.update(ai_provider='gemini', openrouter_model='google/gemini-3.8-flash', clear_openrouter_key='1')
    client.post('/admin/ai-settings', data=data)
    assert db.get_settings()['ai_provider'] == 'gemini' and not db.get_settings()['openrouter_api_key']


def test_admin_guarda_ajustes_y_aprueba(client):
    admin = make_user('adm@x.com', admin=True)
    uid = make_user()
    pid = None
    with app_module.app.test_request_context('/'):
        pid = db.create_payment(uid, 'pago_movil', 'REF12345', 5)
    login(client, admin)

    client.post('/admin/settings', data={
        'free_ai_limit': '3', 'free_manual_enabled': 'on', 'free_manual_limit': '',
        'plan_price_usd': '5', 'plan_days': '30', 'binance_email': '12345678',
    })
    client.get('/admin/')
    with client.session_transaction() as sess:
        token = sess['ai_csrf_token']
    client.post(f'/admin/payments/{pid}/approve', data={'csrf_token': token})

    with app_module.app.test_request_context('/'):
        s = db.get_settings()
        assert s['free_ai_enabled'] == '0'      # checkbox sin marcar
        assert s['free_ai_limit'] == '3'
        assert s['free_manual_enabled'] == '1'
        assert s['binance_email'] == '12345678'
        assert db.has_active_plan(user(uid))


def test_admin_rechaza_limite_invalido(client):
    admin = make_user('adm@x.com', admin=True)
    login(client, admin)
    resp = client.post('/admin/settings', data={
        'free_ai_limit': 'abc', 'plan_price_usd': '5', 'plan_days': '30'})
    assert 'msg=' in resp.headers['Location']
    with app_module.app.test_request_context('/'):
        assert db.get_settings()['free_ai_limit'] == ''


# ── Bibliografía por término en glosarios: solo plan Pro ──────────────

def _glossary_post(client, **extra):
    data = {'title': 'Biologia celular', 'document_kind': 'glossary', 'glossary_source': 'list',
            'glossary_terms': 'Atomo\nCelula', 'incluir_bibliografia': '1'}
    data.update(extra)
    return client.post('/process_form', data=data)


@pytest.mark.parametrize('plan,allowed', [('recharge', False), ('premium', False), ('pro', True)])
def test_bibliografia_de_glosarios_solo_para_pro(client, monkeypatch, plan, allowed):
    db.set_settings({'plans_public_enabled': '1'})
    uid = make_user()
    if plan == 'recharge':
        db.get_db().execute('UPDATE users SET credits = 5 WHERE id = ?', (uid,))
        db.get_db().commit()
    else:
        db.grant_plan(uid, 30, plan)
    with app_module.app.test_request_context('/'):
        assert plans.glossary_bibliography_access(user(uid)) is allowed
    login(client, uid)
    monkeypatch.setattr(app_module, 'check_glossary', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'generate_glossary', lambda *a, **k: [])
    monkeypatch.setattr(app_module, 'check_title', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no')))
    resp = _glossary_post(client)
    if plan == 'recharge':                      # la recarga ni siquiera tiene glosarios
        assert 'glossary_unavailable' in resp.headers['Location']
    elif allowed:
        assert 'glossary_bibliography_unavailable' not in (resp.headers.get('Location') or '')
    else:
        assert resp.status_code == 302 and 'glossary_bibliography_unavailable' in resp.headers['Location']
        assert db.billing_state(user(uid))['used'] == 0                 # no gastó cupo


def test_premium_puede_generar_el_glosario_sin_bibliografia(client, monkeypatch):
    db.set_settings({'plans_public_enabled': '1'})
    uid = make_user()
    db.grant_plan(uid, 30, 'premium')
    login(client, uid)
    seen = []
    monkeypatch.setattr(app_module, 'check_glossary', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'generate_glossary', lambda *a, **k: seen.append(k['bibliography']) or [])
    client.post('/process_form', data={'title': 'Biologia celular', 'document_kind': 'glossary',
                                       'glossary_source': 'list', 'glossary_terms': 'Atomo\nCelula'})
    assert seen == [False]                      # el glosario se genera, sin bibliografía


def test_admin_y_modo_sin_planes_publicos_conservan_la_bibliografia(client):
    admin = make_user('adm@x.com', admin=True)
    free = make_user('free@x.com')
    with app_module.app.test_request_context('/'):
        db.set_settings({'plans_public_enabled': '1'})
        assert plans.glossary_bibliography_access(user(admin)) is True
        assert plans.glossary_bibliography_access(user(free)) is False
        assert plans.glossary_bibliography_access(None) is False
        db.set_settings({'plans_public_enabled': '0'})
        assert plans.glossary_bibliography_access(user(free)) is True


def test_el_formulario_marca_si_la_bibliografia_de_glosarios_esta_permitida(client):
    db.set_settings({'plans_public_enabled': '1'})
    uid = make_user()
    db.grant_plan(uid, 30, 'premium')
    login(client, uid)
    assert 'data-glossary-bib="0"' in client.get('/form').get_data(as_text=True)
    db.grant_plan(uid, 30, 'pro')
    db.get_db().execute("UPDATE users SET plan = 'pro' WHERE id = ?", (uid,))
    db.get_db().commit()
    assert 'data-glossary-bib="1"' in client.get('/form').get_data(as_text=True)
