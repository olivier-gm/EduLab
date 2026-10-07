"""Compras, reintentos y modo gratuito; nunca reclama pagos reales."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import requests

import db
import plans
import binance_payments as bp
from app import app, document_filetypes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'payments.db'))
    monkeypatch.setattr(plans, '_bcv_rate', lambda _: None)
    monkeypatch.setenv('BINANCE_VERIFY_TOKEN', 'fake-client-token')
    monkeypatch.setenv('BINANCE_VERIFY_URL', 'https://verifier.example.invalid')
    app.config['TESTING'] = True
    db.init_db()
    with app.app_context():
        yield app.test_client()


def make_user(admin=False, email='buyer@example.invalid'):
    uid = db.create_user(email, 'Comprador')
    if admin:
        db.get_db().execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
        db.get_db().commit()
    return uid


def login(client, uid):
    with client.session_transaction() as sess:
        sess['user_id'] = uid


def csrf(client):
    client.get('/plans')
    with client.session_transaction() as sess:
        return sess['payment_csrf_token']


def fake_api(monkeypatch, result, http=200):
    calls = []
    class Response:
        status_code = http
        def json(self):
            return result
    def post(url, **kwargs):
        assert url == 'https://verifier.example.invalid/v1/payments/verify'
        assert kwargs['headers']['Authorization'] == 'Bearer fake-client-token'
        assert kwargs['allow_redirects'] is False
        assert kwargs['timeout'] == (5, 90)
        calls.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(bp.requests, 'post', post)
    return calls


def retry_now(pid):
    db.get_db().execute('UPDATE payments SET verify_after = 0 WHERE id = ?', (pid,))
    db.get_db().commit()


def test_default_free_mode_hides_plans_and_preserves_paid_balances(client):
    assert db.get_settings()['plans_public_enabled'] == '0'
    uid = make_user()
    db.grant_plan(uid, 30)
    db.get_db().execute('UPDATE users SET credits = 20, plan_used = 400 WHERE id = ?', (uid,))
    db.get_db().commit()
    db.set_settings({'free_ai_enabled': '0', 'free_manual_enabled': '0',
                     'free_ai_limit': '0', 'free_manual_limit': '0'})
    login(client, uid)
    for path in ('/', '/form', '/my_documents'):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'href="/plans"' not in html and 'Ver planes' not in html
    assert client.get('/plans').status_code == 302
    terms = client.get('/terms').get_data(as_text=True)
    assert 'gratuitamente' in terms and 'Planes, pagos' not in terms and 'plan mensual' not in terms
    assert client.post('/plans/pay', data={}).status_code == 403
    user = db.get_user_by_id(uid)
    assert plans.generation_access(user, 'ai') == (True, None)
    assert plans.generation_access(user, 'manual') == (True, None)
    assert plans.access_summary(user)['ai']['limit'] == 5
    assert db.billing_state(user)['terms'] == 200
    assert db.reserve_generation(uid, allow_free=True)['source'] == 'free'
    assert db.get_user_by_id(uid)['plan_used'] == 400
    assert db.get_user_by_id(uid)['credits'] == 20
    with app.test_request_context('/'):
        assert document_filetypes({'billing_plan': 'recharge'}) == ('docx', 'pdf')
    db.set_settings({'plans_public_enabled': '1'})
    assert plans.generation_access(db.get_user_by_id(uid), 'ai') == (False, 'monthly_limit')
    with app.test_request_context('/'):
        assert document_filetypes({'billing_plan': 'recharge'}) == ('docx',)


def test_admin_toggle_csrf_and_admin_can_buy_while_free(client):
    uid = make_user(admin=True)
    login(client, uid)
    assert client.get('/plans').status_code == 200
    assert 'href="/plans"' in client.get('/').get_data(as_text=True)
    client.get('/admin/')
    with client.session_transaction() as sess:
        token = sess['ai_csrf_token']
    client.post('/admin/plans-visibility', data={'plans_public_enabled': '1', 'csrf_token': 'bad'})
    assert not db.public_plans_enabled()
    client.post('/admin/plans-visibility', data={'plans_public_enabled': '1', 'csrf_token': token})
    assert db.public_plans_enabled()
    client.post('/admin/plans-visibility', data={'plans_public_enabled': '0', 'csrf_token': token})
    assert not db.public_plans_enabled()
    assert plans.generation_access(db.get_user_by_id(uid), 'ai') == (True, None)


@pytest.mark.parametrize('plan_id,price', [('recharge', '2.99'), ('premium', '4.99'), ('pro', '14.99')])
def test_admin_purchase_verified_only_by_api_uses_server_price(client, monkeypatch, plan_id, price):
    uid = make_user(admin=True)
    login(client, uid)
    calls = fake_api(monkeypatch, {'status': 'VERIFIED', 'verified': True})
    token = csrf(client)
    response = client.post('/plans/pay', data={'method': 'binance', 'reference': 'Pay_1234',
                         'plan_id': plan_id, 'amount_usd': '.01', 'csrf_token': token})
    assert response.status_code == 302
    p = db.get_user_payments(uid)[0]
    assert p['status'] == 'approved'
    assert calls[0]['expectedAmount'] == price and isinstance(calls[0]['expectedAmount'], str)
    assert calls[0]['paymentCode'] == 'Pay_1234'
    assert calls[0]['asset'] == 'USDT' and calls[0]['maxAgeMinutes'] == 1440
    assert calls[0]['orderReference'] == p['order_reference']
    u = db.get_user_by_id(uid)
    if plan_id == 'recharge':
        assert u['credits'] == 20
    else:
        assert u['plan'] == plan_id and db.has_active_plan(u)
    bp.verify_payment(p['id'])
    assert len(calls) == 1
    assert dict(db.get_user_by_id(uid)) == dict(u)


@pytest.mark.parametrize('status', ['NOT_FOUND', 'AMOUNT_MISMATCH', 'ALREADY_CLAIMED'])
def test_rejections_do_not_activate_even_for_admin(client, monkeypatch, status):
    uid = make_user(admin=True)
    pid = db.create_payment(uid, 'binance', 'FAKE1234', 4.99)
    fake_api(monkeypatch, {'status': status, 'verified': False})
    result = bp.verify_payment(pid)
    assert result['status'] == 'rejected' and not result['verified']
    assert not db.has_active_plan(db.get_user_by_id(uid))
    assert plans.generation_access(db.get_user_by_id(uid), 'ai') == (True, None)


@pytest.mark.parametrize('status', ['PENDING_SYNC', 'BINANCE_API_UNAVAILABLE', 'NOT_FOUND'])
def test_retry_keeps_order_and_snapshot_activates_exactly_once(client, monkeypatch, status):
    uid = make_user()
    pid = db.create_payment(uid, 'binance', 'FAKE1234', 2.99, 'recharge')
    calls = fake_api(monkeypatch, {'status': status, 'verified': False, 'retryable': True})
    result = bp.verify_payment(pid)
    assert result['retryable'] and result['status'] == 'pending'
    bp.verify_payment(pid)  # reintentar demasiado pronto no llama a la API
    assert len(calls) == 1
    original = calls[0]
    db.set_settings({'recharge_price': '9.99', 'recharge_limit': '50'})
    retry_now(pid)
    calls = fake_api(monkeypatch, {'status': 'VERIFIED', 'verified': True, 'idempotent': True})
    assert bp.verify_payment(pid)['verified']
    assert calls[0] == original
    assert db.get_user_by_id(uid)['credits'] == 20
    assert bp.verify_payment(pid)['verified']
    assert len(calls) == 1 and db.get_user_by_id(uid)['credits'] == 20


@pytest.mark.parametrize('result,http', [
    ({'status': 'VERIFIED', 'verified': 'true'}, 200),
    ({'status': 'VERIFIED', 'verified': 1}, 200),
    ({'status': 'NOT_FOUND', 'verified': True}, 200),
    (None, 200), ({'status': 'UNKNOWN', 'verified': True}, 200),
    ({'status': 'VERIFIED', 'verified': True}, 500),
    ({}, 401), ({}, 422),
])
def test_malformed_or_http_errors_cannot_activate(client, monkeypatch, result, http):
    uid = make_user()
    pid = db.create_payment(uid, 'binance', 'FAKE1234', 4.99)
    fake_api(monkeypatch, result, http)
    assert bp.verify_payment(pid)['status'] == 'pending'
    assert not db.has_active_plan(db.get_user_by_id(uid))


def test_timeout_after_remote_claim_can_retry_idempotently(client, monkeypatch):
    uid = make_user()
    pid = db.create_payment(uid, 'binance', 'FAKE1234', 4.99)
    def timeout(*a, **kw):
        raise requests.Timeout('respuesta perdida')
    monkeypatch.setattr(bp.requests, 'post', timeout)
    assert bp.verify_payment(pid)['retryable']
    ref = db.get_payment(pid)['order_reference']
    retry_now(pid)
    calls = fake_api(monkeypatch, {'status': 'VERIFIED', 'verified': True, 'idempotent': True})
    assert bp.verify_payment(pid)['verified']
    assert calls[0]['orderReference'] == ref


def test_payment_csrf_and_ownership_and_admin_manual_bypass_blocked(client, monkeypatch):
    uid = make_user(admin=True)
    other = make_user(email='other@example.invalid')
    login(client, uid)
    token = csrf(client)
    calls = fake_api(monkeypatch, {'status': 'NOT_FOUND', 'verified': False})
    assert client.post('/plans/pay', data={'method': 'binance', 'reference': 'FAKE1234'}).status_code == 400
    assert not db.get_user_payments(uid)
    pid = db.create_payment(other, 'binance', 'FAKE1234', 4.99)
    assert client.post(f'/plans/payments/{pid}/verify', data={'csrf_token': token}).status_code == 404
    assert not calls
    client.get('/admin/')
    with client.session_transaction() as sess:
        admin_token = sess['ai_csrf_token']
    client.post(f'/admin/payments/{pid}/approve')
    assert not calls and db.get_payment(pid)['status'] == 'pending'
    client.post(f'/admin/payments/{pid}/reject', data={'csrf_token': admin_token})
    assert db.get_payment(pid)['status'] == 'pending'
    client.post(f'/admin/payments/{pid}/approve', data={'csrf_token': admin_token})
    assert len(calls) == 1 and db.get_payment(pid)['status'] == 'rejected'
    assert not db.has_active_plan(db.get_user_by_id(other))


def test_duplicate_reference_atomic_across_accounts(client):
    ids = [make_user(email=f'buyer{i}@example.invalid') for i in range(2)]
    barrier = Barrier(2)
    def submit(uid):
        with app.app_context():
            barrier.wait()
            try:
                return db.create_payment(uid, 'binance', 'SAME1234', 4.99)
            except ValueError:
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ids))
    assert sum(r is not None for r in results) == 1


def test_verification_lease_prevents_parallel_requests_and_stale_results(client):
    uid = make_user()
    pid = db.create_payment(uid, 'binance', 'FAKE1234', 4.99)
    attempt = db.begin_payment_verification(pid)
    assert db.begin_payment_verification(pid) is None
    assert not db.review_payment(pid, False, uid, 30)
    retry_now(pid)
    newer = db.begin_payment_verification(pid)
    assert attempt['order_reference'] == newer['order_reference']
    db.finish_payment_verification(pid, attempt['verify_attempt'], 'NOT_FOUND', 'rejected')
    assert db.get_payment(pid)['status'] == 'pending'
    db.finish_payment_verification(pid, newer['verify_attempt'], 'VERIFIED', 'approved')
    assert db.get_payment(pid)['status'] == 'approved'


def test_unconfigured_pending_order_is_kept_without_calling_api(client, monkeypatch):
    uid = make_user()
    pid = db.create_payment(uid, 'binance', 'FAKE1234', 4.99)
    monkeypatch.setenv('BINANCE_VERIFY_TOKEN', '')
    monkeypatch.setattr(bp.requests, 'post', lambda *a, **k: pytest.fail('No hay token'))
    result = bp.verify_payment(pid)
    assert result['status'] == 'pending' and result['provider_status'] == 'CONFIG_ERROR'
    assert not result['retryable']


def test_correct_missing_reference_preserves_uncertain_payments(client, monkeypatch):
    uid = make_user(admin=True)
    login(client, uid)
    token = csrf(client)
    pid = db.create_payment(uid, 'binance', 'WRONG1234', 4.99)
    calls = fake_api(monkeypatch, {'status': 'NOT_FOUND', 'verified': False, 'retryable': True})
    bp.verify_payment(pid)
    assert db.get_payment(pid)['provider_status'] == 'NOT_FOUND'
    html = client.get('/plans').get_data(as_text=True)
    assert 'Corregir ID de pago' in html and 'data-binance-payment' in html
    assert client.post(f'/plans/payments/{pid}/correct').status_code == 400
    assert db.get_payment(pid)['status'] == 'pending'
    client.post(f'/plans/payments/{pid}/correct', data={'csrf_token': token})
    assert db.get_payment(pid)['provider_status'] == 'USER_CANCELLED'
    assert db.get_payment(pid)['status'] == 'rejected' and len(calls) == 1
    pid2 = db.create_payment(uid, 'binance', 'OTHER1234', 4.99)
    lease = db.begin_payment_verification(pid2)
    assert not db.cancel_missing_payment(pid2, uid)
    db.finish_payment_verification(pid2, lease['verify_attempt'], 'SERVICE_ERROR', 'pending')
    assert not db.cancel_missing_payment(pid2, uid)


def test_remote_claim_with_local_commit_failure_recovers_same_order(client, monkeypatch):
    uid = make_user()
    pid = db.create_payment(uid, 'binance', 'FAKE1234', 2.99, 'recharge')
    calls = fake_api(monkeypatch, {'status': 'VERIFIED', 'verified': True})
    finish = db.finish_payment_verification
    def broken(*args):
        raise RuntimeError('fallo de base de datos después de reclamar el pago')
    monkeypatch.setattr(db, 'finish_payment_verification', broken)
    with pytest.raises(RuntimeError):
        bp.verify_payment(pid)
    assert db.get_payment(pid)['status'] == 'pending'
    assert db.get_user_by_id(uid)['credits'] == 0
    monkeypatch.setattr(db, 'finish_payment_verification', finish)
    retry_now(pid)
    assert bp.verify_payment(pid)['verified']
    assert calls[0] == calls[1] and db.get_user_by_id(uid)['credits'] == 20


def test_free_mode_uses_general_retention_only_for_new_documents(client):
    from datetime import datetime, timedelta
    uid = make_user()
    db.set_settings({'plans_public_enabled': '1', 'file_retention_hours': '48'})
    db.grant_plan(uid, 30, 'pro')
    db.record_document(uid, 'Anterior', 'uni', 0, file_stem='fake-old')
    old = dict(db.list_user_documents(uid)[0])
    db.set_settings({'plans_public_enabled': '0'})
    db.record_document(uid, 'Nuevo gratuito', 'uni', 0, file_stem='fake-new')
    new, previous = db.list_user_documents(uid)
    assert dict(previous) == old
    assert datetime.strptime(new['expires_at'], db.DATETIME_FMT) - datetime.strptime(new['created_at'], db.DATETIME_FMT) == timedelta(hours=48)
