"""Registro cerrado hasta verificar correo; SMTP simulado, sin envíos reales."""
import smtplib
from types import SimpleNamespace

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import auth
import db
import mail_service
from app import app


@pytest.fixture
def registration(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'registration.db'))
    db.init_db()
    monkeypatch.setenv('GMAIL_USER', 'sender@example.test')
    monkeypatch.setenv('GMAIL_APP_PASSWORD', 'test-password')
    clock = [1800000000]
    monkeypatch.setattr(auth.time, 'time', lambda: clock[0])
    messages = []
    monkeypatch.setattr(mail_service, 'send_verification', lambda email, name, code, **kwargs: messages.append((email, name, code)))
    with app.test_client() as client, app.app_context():
        client.get('/register')
        with client.session_transaction() as session:
            csrf = session['registration_csrf']
        yield client, csrf, clock, messages


def start(client, csrf, email='new@example.test'):
    return client.post('/register', data={'name': 'Ana', 'email': email, 'password': 'password123', 'password_confirm': 'password123', 'csrf_token': csrf}, follow_redirects=True)


def verify(client, csrf, code):
    return client.post('/register/verify', data={'code': code, 'csrf_token': csrf})


def test_no_account_until_code_and_one_time_verification(registration):
    client, csrf, clock, messages = registration
    assert 'Verificar y crear cuenta' in start(client, csrf).get_data(as_text=True)
    assert db.get_user_by_email('new@example.test') is None
    with client.session_transaction() as session:
        token = session['registration_token']
        assert 'user_id' not in session and 'password' not in session and 'code' not in session
    pending = db.pending_registration(token)
    code = messages[-1][2]
    assert pending['code_hash'] != code and pending['password_hash'] != 'password123'
    assert 'incorrecto' in verify(client, csrf, 'not-a-code').get_data(as_text=True)
    assert db.get_user_by_email('new@example.test') is None
    assert 'Correo o contraseña incorrectos' in client.post('/login', data={
        'email': 'new@example.test', 'password': 'password123'}).get_data(as_text=True)
    assert verify(client, csrf, code).status_code == 302
    user = db.get_user_by_email('new@example.test')
    assert check_password_hash(user['password_hash'], 'password123')
    assert db.pending_registration(token) is None
    with client.session_transaction() as session:
        assert session['user_id'] == user['id'] and 'registration_token' not in session
    assert db.complete_registration(token, pending['code_hash'], clock[0])[0] is None


def test_expiry_attempts_resend_and_old_code_invalidation(registration, monkeypatch):
    client, csrf, clock, messages = registration
    codes = iter([123456, 654321, 765432])
    monkeypatch.setattr(auth.secrets, 'randbelow', lambda _: next(codes))
    start(client, csrf)
    for _ in range(5):
        verify(client, csrf, '000000')
    assert 'límite de intentos' in verify(client, csrf, '123456').get_data(as_text=True)
    assert 'un minuto' in client.post('/register/verify', data={'action': 'resend', 'csrf_token': csrf}).get_data(as_text=True)
    clock[0] += 61
    assert 'Enviamos un nuevo código' in client.post('/register/verify', data={'action': 'resend', 'csrf_token': csrf}).get_data(as_text=True)
    assert messages[-1][2] == '765432'  # el intento de reenvío bloqueado tampoco se manda
    assert 'incorrecto' in verify(client, csrf, '123456').get_data(as_text=True)
    clock[0] += 600
    assert 'venció' in verify(client, csrf, messages[-1][2]).get_data(as_text=True)
    assert db.get_user_by_email('new@example.test') is None


def test_missing_smtp_failure_csrf_and_cooldown_across_sessions(registration, monkeypatch):
    client, csrf, clock, messages = registration
    assert start(client, 'bad-csrf').status_code == 403 and not messages
    monkeypatch.delenv('GMAIL_APP_PASSWORD')
    assert 'no está disponible' in start(client, csrf).get_data(as_text=True)
    assert db.get_user_by_email('new@example.test') is None
    monkeypatch.setenv('GMAIL_APP_PASSWORD', 'test-password')
    def fail(*args):
        raise smtplib.SMTPAuthenticationError(535, b'bad credentials')
    monkeypatch.setattr(mail_service, 'send_verification', fail)
    assert 'No pudimos enviar' in start(client, csrf).get_data(as_text=True)
    assert db.get_user_by_email('new@example.test') is None
    with client.session_transaction() as session:
        session.clear()
    client.get('/register')
    with client.session_transaction() as session:
        other_csrf = session['registration_csrf']
    assert 'un minuto' in start(client, other_csrf).get_data(as_text=True)


def test_google_verified_accounts_bypass_email_code(registration, monkeypatch):
    client, csrf, clock, messages = registration
    monkeypatch.setattr(auth, 'google_oauth_enabled', lambda: True)
    info = {'sub': 'google-test', 'email': 'google@example.test', 'name': 'Google', 'email_verified': True}
    monkeypatch.setattr(auth.requests, 'post', lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'access_token': 'test-token'}))
    monkeypatch.setattr(auth.requests, 'get', lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: info))
    with client.session_transaction() as session:
        session['oauth_state'] = 'test-state'
    assert client.get('/auth/google/callback?state=test-state&code=test-code').status_code == 302
    assert db.get_user_by_email(info['email']) and not messages
    with client.session_transaction() as session:
        session.clear()
        session['oauth_state'] = 'another-state'
    info.update(sub='unverified', email='unverified@example.test', email_verified=False)
    client.get('/auth/google/callback?state=another-state&code=test-code')
    assert db.get_user_by_email(info['email']) is None


def test_hourly_send_limit_and_single_winner_for_simultaneous_verification(registration):
    from concurrent.futures import ThreadPoolExecutor
    client, csrf, clock, messages = registration
    start(client, csrf)
    for _ in range(4):
        clock[0] += 61
        client.post('/register/verify', data={'action': 'resend', 'csrf_token': csrf})
    assert len(messages) == 5
    clock[0] += 61
    assert 'límite de envíos' in client.post('/register/verify', data={
        'action': 'resend', 'csrf_token': csrf}).get_data(as_text=True)
    assert len(messages) == 5
    with client.session_transaction() as session:
        token = session['registration_token']
    digest = auth._registration_hash(token + ':' + messages[-1][2])
    def complete():
        with app.app_context():
            return db.complete_registration(token, digest, clock[0])[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: complete(), range(2)))
    assert sum(uid is not None for uid in results) == 1


def test_email_gmail_tls_sender_html_and_plaintext(monkeypatch):
    sent = []
    monkeypatch.setenv('GMAIL_USER', 'sender@example.test')
    monkeypatch.setenv('GMAIL_APP_PASSWORD', 'abcd efgh ijkl mnop')
    monkeypatch.delenv('MAIL_LOGO_URL', raising=False)
    class SMTP:
        def __init__(self, host, port, timeout, context):
            assert (host, port, timeout) == ('smtp.gmail.com', 465, 15)
            assert context.check_hostname
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, user, password):
            assert user == 'sender@example.test' and password == 'abcdefghijklmnop'
        def send_message(self, message):
            sent.append(message)
            return {}
    monkeypatch.setattr(mail_service.smtplib, 'SMTP_SSL', SMTP)
    with app.app_context():
        mail_service.send_verification('new@example.test', '<script>Ana</script>', '012345')
    message = sent[0]
    assert str(message['Subject']).startswith('012345 es tu código de verificación')
    assert str(message['From']) == 'Edu Lab <sender@example.test>'
    assert '012345' in message.get_body(preferencelist=('plain',)).get_content()
    html = message.get_body(preferencelist=('html',)).get_content()
    assert '012345' in html and '#20e4da' in html
    assert '<img' not in html.lower() and 'logo_url' not in html
    assert '&lt;script&gt;' in html and '<script>Ana' not in html
    assert message.get_content_type() == 'multipart/alternative'
    assert not list(message.iter_attachments())
    assert not any(part.get_content_maintype() == 'image' or part.get_filename() for part in message.walk())
    assert 'cid:' not in html and message['Date'] and message['Message-ID']
    assert 'Tu código de EduLab es <strong>012345</strong>' in html
    assert 'name="color-scheme" content="dark"' in html
    assert 'display:none' not in html and 'display: none' not in html
    monkeypatch.setenv('MAIL_LOGO_URL', 'https://edulab.wiki/static/img/icon.png')
    with app.app_context():
        mail_service.send_verification('new@example.test', 'Ana', '654321', purpose='reset')
    assert str(sent[-1]['Subject']).startswith('654321 es tu código para recuperar tu contraseña')
    assert 'nueva contraseña' in sent[-1].get_body(preferencelist=('html',)).get_content()
    reset_html = sent[-1].get_body(preferencelist=('html',)).get_content()
    assert '<img' not in reset_html.lower() and 'https://edulab.wiki/static/img/icon.png' not in reset_html
    assert str(sent[-1]['From']) == 'Edu Lab <sender@example.test>'
    assert not list(sent[-1].iter_attachments())


def test_registration_six_characters_and_confirmation(registration):
    client, csrf, clock, messages = registration
    form = {'name': 'Ana', 'email': 'short@example.test', 'csrf_token': csrf,
            'password': '12345', 'password_confirm': '12345'}
    assert 'entre 6 y 256' in client.post('/register', data=form).get_data(as_text=True)
    form.update(password='123456', password_confirm='654321')
    assert 'no coinciden' in client.post('/register', data=form).get_data(as_text=True)
    assert not messages and not db.get_user_by_email(form['email'])
    form['password_confirm'] = form['password']
    assert client.post('/register', data=form).status_code == 302
    assert verify(client, csrf, messages[-1][2]).status_code == 302
    assert check_password_hash(db.get_user_by_email(form['email'])['password_hash'], '123456')


def test_reset_requires_code_confirms_password_and_invalidates_sessions(registration):
    client, csrf, clock, messages = registration
    uid = db.create_user('recover@example.test', 'Ana', generate_password_hash('original'))
    old_client = app.test_client()
    with old_client.session_transaction() as session:
        session['user_id'] = uid  # también invalida sesiones antiguas sin auth_version
    assert 'Olvidé mi contraseña' in client.get('/login').get_data(as_text=True)
    assert client.post('/forgot-password', data={'email': 'recover@example.test', 'csrf_token': csrf}).status_code == 302
    code = messages[-1][2]
    with client.session_transaction() as session:
        token = session['reset_token']
    form = {'action': 'change', 'password': 'new123', 'password_confirm': 'new123', 'csrf_token': csrf}
    assert 'Verifica un código' in client.post('/reset-password', data=form).get_data(as_text=True)
    assert check_password_hash(db.get_user_by_id(uid)['password_hash'], 'original')
    assert client.post('/reset-password', data={'code': code, 'csrf_token': 'invalid'}).status_code == 403
    assert 'incorrecto' in client.post('/reset-password', data={'code': 'bad-code', 'csrf_token': csrf}).get_data(as_text=True)
    assert client.post('/reset-password', data={'code': code, 'csrf_token': csrf}).status_code == 302
    assert 'Nueva contraseña' in client.get('/reset-password').get_data(as_text=True)
    assert db.verify_password_reset(token, '', clock[0])  # código consumido; no prolonga el permiso
    form['password_confirm'] = 'different'
    assert 'no coinciden' in client.post('/reset-password', data=form).get_data(as_text=True)
    form.update(password='short', password_confirm='short')
    assert 'entre 6 y 256' in client.post('/reset-password', data=form).get_data(as_text=True)
    form.update(password='new123', password_confirm='new123')
    response = client.post('/reset-password', data=form)
    assert response.status_code == 302 and '/login?reset=1' in response.headers['Location']
    user = db.get_user_by_id(uid)
    assert user['auth_version'] == 1 and check_password_hash(user['password_hash'], 'new123')
    assert not check_password_hash(user['password_hash'], 'original')
    assert db.pending_registration(token, 'reset') is None
    assert old_client.get('/my_documents').status_code == 302
    with old_client.session_transaction() as session:
        assert 'user_id' not in session
    assert 'incorrectos' in client.post('/login', data={'email': user['email'], 'password': 'original'}).get_data(as_text=True)
    assert client.post('/login', data={'email': user['email'], 'password': 'new123'}).status_code == 302
    with client.session_transaction() as session:
        assert session['auth_version'] == 1


def test_reset_unknown_google_accounts_expiry_resend_and_purpose_isolation(registration, monkeypatch):
    client, csrf, clock, messages = registration
    uid = db.create_user('google-only@example.test', 'Google', google_id='google-only')
    for email in ('missing@example.test', 'google-only@example.test'):
        assert client.post('/forgot-password', data={'email': email, 'csrf_token': csrf}).status_code == 302
        assert 'Si ' in client.get('/reset-password').get_data(as_text=True)
    assert not messages and db.get_user_by_id(uid)['password_hash'] is None
    uid = db.create_user('normal@example.test', 'Ana', generate_password_hash('original'))
    codes = iter([111111, 222222])
    monkeypatch.setattr(auth.secrets, 'randbelow', lambda _: next(codes))
    client.post('/forgot-password', data={'email': 'normal@example.test', 'csrf_token': csrf})
    with client.session_transaction() as session:
        token = session['reset_token']
    digest = auth._registration_hash(token + ':111111')
    assert db.complete_registration(token, digest, clock[0])[0] is None
    clock[0] += 601
    assert 'venció' in client.post('/reset-password', data={'code': '111111', 'csrf_token': csrf}).get_data(as_text=True)
    assert 'enviamos un nuevo código' in client.post('/reset-password', data={
        'action': 'resend', 'csrf_token': csrf}).get_data(as_text=True)
    assert 'incorrecto' in client.post('/reset-password', data={'code': '111111', 'csrf_token': csrf}).get_data(as_text=True)
    assert client.post('/reset-password', data={'code': '222222', 'csrf_token': csrf}).status_code == 302
    clock[0] += 600
    assert 'Verifica un código' in client.post('/reset-password', data={'action': 'change',
        'password': 'new123', 'password_confirm': 'new123', 'csrf_token': csrf}).get_data(as_text=True)
    assert check_password_hash(db.get_user_by_id(uid)['password_hash'], 'original')
