# auth.py
"""Registro/login normal y 'Continuar con Google' (OAuth 2.0 manual, sin
Authlib: el flujo es simple y `requests` ya es una dependencia del
proyecto). El registro normal crea la cuenta solo después de verificar el
código por correo; Google confirma el correo directamente.
"""

import os
import re
import secrets
import logging
import hashlib
import hmac
import time
import smtplib
from functools import wraps
from urllib.parse import urlencode

import requests
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash

import db
import rate_limit
import mail_service

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', '')

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'


def google_oauth_enabled():
    """El botón 'Continuar con Google' sólo se muestra si las tres variables
    están configuradas; si falta alguna, se oculta en vez de romper la
    página (ver GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI en .env.example)."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = db.get_user_by_id(user_id)
    if not user or session.get('auth_version', 0) != user['auth_version']:
        session.clear()
        return None
    return user


@auth_bp.before_app_request
def validate_session():
    if session.get('user_id'):
        current_user()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for('auth.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or not user['is_admin']:
            return redirect(url_for('welcome'))
        return view(*args, **kwargs)
    return wrapped


def _log_in_as(user_id):
    session.clear()
    session['user_id'] = user_id
    session['auth_version'] = db.get_user_by_id(user_id)['auth_version']


def _registration_hash(value):
    return hmac.new(str(current_app.secret_key).encode(), value.encode(), hashlib.sha256).hexdigest()


def _registration_csrf_valid():
    expected = session.get('registration_csrf')
    return bool(expected and secrets.compare_digest(expected, request.form.get('csrf_token', '')))


def _send_registration(email, name, password_hash, purpose='register'):
    if not mail_service.mail_configured():
        return 'El envío de códigos no está disponible por ahora. Inténtalo más tarde o accede con Google.'
    token = secrets.token_urlsafe(32)
    code = f'{secrets.randbelow(1000000):06d}'
    try:
        db.begin_registration(email, name, password_hash, token, _registration_hash(token + ':' + code),
                              int(time.time()), _registration_hash(request.remote_addr or 'unknown'), purpose)
    except ValueError as exc:
        return str(exc)
    session['registration_token' if purpose == 'register' else 'reset_token'] = token
    try:
        if purpose == 'reset':
            user = db.get_user_by_email(email)
            if user and user['password_hash']:
                mail_service.send_verification(email, name, code, purpose='reset')
        else:
            mail_service.send_verification(email, name, code)
    except (smtplib.SMTPException, OSError):
        logger.error('No se pudo enviar el correo de verificación por Gmail.')
        if purpose == 'reset':
            return None  # La respuesta pública no revela si existe una cuenta.
        return 'No pudimos enviar el código. Comprueba el correo y vuelve a solicitarlo en un minuto.'
    return None


@auth_bp.route('/register', methods=['GET', 'POST'])
@rate_limit.rate_limit(10, 60, key_func=rate_limit.per_ip)
def register():
    if session.get('user_id'):
        return redirect(url_for('welcome'))

    session.setdefault('registration_csrf', secrets.token_urlsafe(24))
    if request.method == 'GET':
        if request.args.get('restart') == '1':
            session.pop('registration_token', None)
        elif session.get('registration_token') and db.pending_registration(session['registration_token']):
            return redirect(url_for('auth.verify_registration'))
    error = None
    name = email = ''
    if request.method == 'POST':
        if not _registration_csrf_valid():
            return render_template('register.html', error='Recarga la página y vuelve a intentarlo.',
                                   google_enabled=google_oauth_enabled()), 403
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not name or len(name) > 60:
            error = 'Escribe tu nombre.'
        elif len(email) > 254 or not EMAIL_RE.fullmatch(email):
            error = 'Ese correo no parece válido.'
        elif not 6 <= len(password) <= 256:
            error = 'La contraseña debe tener entre 6 y 256 caracteres.'
        elif password != request.form.get('password_confirm', ''):
            error = 'Las contraseñas no coinciden.'
        elif db.get_user_by_email(email):
            error = 'Ya existe una cuenta con ese correo. Inicia sesión.'
        elif email in db.ADMIN_EMAILS:
            user_id = db.create_configured_admin(email, name, generate_password_hash(password))
            if user_id:
                _log_in_as(user_id)
                return redirect(url_for('welcome'))
            error = 'Ya existe una cuenta con ese correo. Inicia sesión.'
        else:
            error = _send_registration(email, name, generate_password_hash(password))
            if session.get('registration_token'):
                if error is None:
                    return redirect(url_for('auth.verify_registration'))
                return _verification_page(error)

    return render_template('register.html', error=error, name=name, email=email, google_enabled=google_oauth_enabled())


def _verification_page(error=None, sent=False):
    row = db.pending_registration(session.get('registration_token', ''))
    if not row:
        session.pop('registration_token', None)
        return redirect(url_for('auth.register'))
    return render_template('register.html', verify=True, email=row['email'], error=error, sent=sent,
                           google_enabled=google_oauth_enabled(),
                           resend_wait=max(0, 60 - (int(time.time()) - row['sent_at'])))


@auth_bp.route('/register/verify', methods=['GET', 'POST'])
def verify_registration():
    if session.get('user_id'):
        return redirect(url_for('welcome'))
    row = db.pending_registration(session.get('registration_token', ''))
    if not row:
        return redirect(url_for('auth.register'))
    if request.method == 'POST':
        if not _registration_csrf_valid():
            return _verification_page('Recarga la página y vuelve a intentarlo.'), 403
        if request.form.get('action') == 'resend':
            error = _send_registration(row['email'], row['name'], row['password_hash'])
            return _verification_page(error, sent=error is None)
        code = request.form.get('code', '').strip()
        # Las entradas inválidas también consumen intento; el límite está en la base de datos.
        digest = _registration_hash(row['token'] + ':' + code) if re.fullmatch(r'[0-9]{6}', code) else ''
        uid, error = db.complete_registration(row['token'], digest, int(time.time()))
        if uid:
            _log_in_as(uid)
            return redirect(url_for('welcome'))
        return _verification_page(error)
    return _verification_page()


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@rate_limit.rate_limit(10, 60, key_func=rate_limit.per_ip)
def forgot_password():
    if session.get('user_id'):
        return redirect(url_for('welcome'))
    session.setdefault('registration_csrf', secrets.token_urlsafe(24))
    error = None
    email = ''
    if request.method == 'POST':
        if not _registration_csrf_valid():
            return render_template('password_recovery.html', stage='email', error='Recarga la página y vuelve a intentarlo.'), 403
        email = request.form.get('email', '').strip().lower()
        if len(email) > 254 or not EMAIL_RE.fullmatch(email):
            error = 'Escribe un correo válido.'
        else:
            user = db.get_user_by_email(email)
            error = _send_registration(email, user['name'] if user else 'Usuario', '', purpose='reset')
            if error is None:
                return redirect(url_for('auth.reset_password'))
    return render_template('password_recovery.html', stage='email', error=error, email=email)


def _reset_page(error=None, sent=False):
    row = db.pending_registration(session.get('reset_token', ''), 'reset')
    if not row:
        return redirect(url_for('auth.forgot_password'))
    stage = 'password' if row['verified_until'] > int(time.time()) else 'code'
    return render_template('password_recovery.html', stage=stage, error=error, sent=sent, email=row['email'])


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if session.get('user_id'):
        return redirect(url_for('welcome'))
    token = session.get('reset_token', '')
    row = db.pending_registration(token, 'reset')
    if not row:
        return redirect(url_for('auth.forgot_password'))
    if request.method == 'POST':
        if not _registration_csrf_valid():
            return _reset_page('Recarga la página y vuelve a intentarlo.'), 403
        action = request.form.get('action', 'verify')
        if action == 'resend':
            error = _send_registration(row['email'], row['name'], '', purpose='reset')
            return _reset_page(error, sent=error is None)
        if action == 'change':
            if row['verified_until'] <= int(time.time()):
                return _reset_page('Verifica un código vigente antes de cambiar la contraseña.')
            password = request.form.get('password', '')
            if not 6 <= len(password) <= 256:
                return _reset_page('La contraseña debe tener entre 6 y 256 caracteres.')
            if password != request.form.get('password_confirm', ''):
                return _reset_page('Las contraseñas no coinciden.')
            if not db.complete_password_reset(token, generate_password_hash(password), int(time.time())):
                return _reset_page('No se pudo cambiar la contraseña. Solicita un nuevo código.')
            session.clear()
            return redirect(url_for('auth.login', reset=1))
        code = request.form.get('code', '').strip()
        digest = _registration_hash(token + ':' + code) if re.fullmatch(r'[0-9]{6}', code) else ''
        error = db.verify_password_reset(token, digest, int(time.time()))
        if error:
            return _reset_page(error)
        return redirect(url_for('auth.reset_password'))
    return _reset_page()


@auth_bp.route('/login', methods=['GET', 'POST'])
@rate_limit.rate_limit(10, 60, key_func=rate_limit.per_ip)
def login():
    if session.get('user_id'):
        return redirect(url_for('welcome'))

    next_url = request.values.get('next') or url_for('welcome')
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = db.get_user_by_email(email)

        if not user or not user['password_hash'] or not check_password_hash(user['password_hash'], password):
            error = 'Correo o contraseña incorrectos.'
        else:
            db.touch_admin_status(user)
            _log_in_as(user['id'])
            return redirect(next_url)

    return render_template('login.html', error=error, next=next_url, google_enabled=google_oauth_enabled())


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('welcome'))


@auth_bp.route('/auth/google')
def google_start():
    if not google_oauth_enabled():
        return redirect(url_for('auth.login'))

    state = secrets.token_urlsafe(24)
    session['oauth_state'] = state
    session['oauth_next'] = request.args.get('next') or url_for('welcome')

    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'prompt': 'select_account',
    }
    return redirect(f'{GOOGLE_AUTH_URL}?{urlencode(params)}')


@auth_bp.route('/auth/google/callback')
def google_callback():
    if not google_oauth_enabled():
        return redirect(url_for('auth.login'))

    expected_state = session.pop('oauth_state', None)
    if not expected_state or request.args.get('state') != expected_state:
        logger.warning('Estado OAuth inválido al volver de Google (posible CSRF).')
        return redirect(url_for('auth.login'))

    code = request.args.get('code')
    if not code:
        return redirect(url_for('auth.login'))

    try:
        token_resp = requests.post(GOOGLE_TOKEN_URL, data={
            'code': code,
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'redirect_uri': GOOGLE_REDIRECT_URI,
            'grant_type': 'authorization_code',
        }, timeout=10)
        token_resp.raise_for_status()
        access_token = token_resp.json().get('access_token')

        info_resp = requests.get(
            GOOGLE_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10,
        )
        info_resp.raise_for_status()
        info = info_resp.json()
    except requests.RequestException as e:
        logger.error('Error en el intercambio OAuth con Google: %s', e)
        return redirect(url_for('auth.login'))

    google_id = info.get('sub')
    email = (info.get('email') or '').strip().lower()
    name = info.get('name') or (email.split('@')[0] if email else 'Usuario')

    if not google_id or not email or info.get('email_verified') not in (True, 'true'):
        logger.error('Respuesta de Google sin sub/email, no se puede iniciar sesión.')
        return redirect(url_for('auth.login'))

    user = db.get_user_by_google_id(google_id)
    if not user:
        # Puede que ya tenga cuenta por registro normal con el mismo correo:
        # se enlaza en vez de crear un usuario duplicado.
        user = db.get_user_by_email(email)
        if user:
            db.link_google_id(user['id'], google_id)
        else:
            user_id = db.create_user(email, name, google_id=google_id)
            user = db.get_user_by_id(user_id)

    # Hay que leer oauth_next ANTES de _log_in_as: ese helper hace
    # session.clear() para arrancar la sesión limpia, y se llevaría la
    # clave por delante si se leyera después.
    next_url = session.get('oauth_next') or url_for('welcome')
    db.touch_admin_status(user)
    _log_in_as(user['id'])
    return redirect(next_url)
