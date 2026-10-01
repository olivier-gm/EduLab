# admin.py
"""Panel admin: usuarios, tokens de Gemini, documentos, y la gestión de
planes de pago: reglas para usuarios sin plan, datos de cobro, aprobación
de pagos y activar/quitar el plan a mano."""

import hmac
import os
import re
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, session

import db
import ai_provider
import IA
from auth import admin_required, current_user

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Campos de texto libres de "Datos de cobro" y su largo máximo.
PAYMENT_TEXT_FIELDS = {
    'plan_name': 60,
    'binance_email': 120,
    'pm_bank': 80,
    'pm_phone': 30,
    'pm_id': 30,
    'pm_holder': 80,
}


def _back(message=None):
    return redirect(url_for('admin.dashboard', msg=message) + '#planes')


def _limit_field(raw):
    """'' → sin límite; número entero ≥ 0; otra cosa → None (inválido)."""
    raw = (raw or '').strip()
    if raw == '':
        return ''
    if raw.isdigit():
        return str(int(raw))
    return None


@admin_bp.route('/')
@admin_required
def dashboard():
    values = db.get_settings()
    key_status = {
        prefix: 'Guardada en el panel' if values[f'{prefix}_api_key'] else (
            'Configurada en el servidor' if os.environ.get(env_name) else 'Sin configurar')
        for prefix, env_name in [('gemini', 'GEMINI_API_KEY'), ('openrouter', 'OPENROUTER_API_KEY')]
    }
    return render_template(
        'admin.html',
        stats=db.get_stats(),
        users=db.list_users(),
        documents=db.list_documents(limit=100),
        settings=values,
        gemini_default_model=IA.MODEL_NAME,
        key_status=key_status,
        ai_csrf_token=session.setdefault('ai_csrf_token', secrets.token_urlsafe(32)),
        payments=db.list_payments(limit=100),
        active_plan=db.has_active_plan,
        plan_expiry=db.plan_expiry,
        msg=request.args.get('msg'),
    )


@admin_bp.route('/ai-settings', methods=['POST'])
@admin_required
def save_ai_settings():
    def back(message):
        return redirect(url_for('admin.dashboard', msg=message) + '#ia')
    token = session.get('ai_csrf_token')
    if not token or not hmac.compare_digest(token, request.form.get('csrf_token', '')):
        return back('La sesión del formulario venció. Recarga el panel y vuelve a guardar.')
    provider = request.form.get('ai_provider')
    if provider not in ('gemini', 'openrouter'):
        return back('Elige Gemini directo u OpenRouter.')
    values = {'ai_provider': provider}
    for prefix, default in [('gemini', IA.MODEL_NAME), ('openrouter', 'google/gemini-3.8-flash')]:
        model = (request.form.get(f'{prefix}_model') or default).strip()
        expected = 'google/gemini-' if prefix == 'openrouter' else 'gemini-'
        if len(model) > 160 or not model.startswith(expected) or not re.fullmatch(r'[A-Za-z0-9._/-]+', model):
            return back(f'Escribe el ID de Gemini correcto: {expected}…')
        values[f'{prefix}_model'] = model
        key = (request.form.get(f'{prefix}_api_key') or '').strip()
        if key:
            if len(key) > 512 or any(char.isspace() for char in key):
                return back('La clave API no debe contener espacios ni superar 512 caracteres.')
            try:
                values[f'{prefix}_api_key'] = ai_provider.encrypt_key(key)
            except (OSError, ValueError):
                return back('No se pudo guardar la clave API. Revisa los permisos del servidor.')
        elif request.form.get(f'clear_{prefix}_key'):
            values[f'{prefix}_api_key'] = ''
    proposed = {**db.get_settings(), **values}
    try:
        _, _, active_key = ai_provider.configuration(values=proposed)
    except IA.GenerationError as exc:
        return back(exc.user_message)
    if not active_key:
        return back('Añade la clave API del proveedor que quieres activar antes de guardar.')
    db.set_settings(values)
    IA._search_blocked_until = 0.0
    return back('Configuración de IA guardada. Se aplicará a las siguientes generaciones.')


@admin_bp.route('/settings', methods=['POST'])
@admin_required
def save_settings():
    form = request.form
    values = {}

    # Usuarios sin plan: por modo, activado + límite por usuario.
    for mode in ('ai', 'manual'):
        values[f'free_{mode}_enabled'] = '1' if form.get(f'free_{mode}_enabled') else '0'
        limit = _limit_field(form.get(f'free_{mode}_limit'))
        if limit is None:
            return _back('El límite debe ser un número entero (o vacío para ilimitado).')
        values[f'free_{mode}_limit'] = limit

    # Plan
    try:
        price = float((form.get('plan_price_usd') or '').replace(',', '.'))
        days = int(form.get('plan_days') or '')
        rate_raw = (form.get('bs_rate') or '').replace(',', '.').strip()
        rate = float(rate_raw) if rate_raw else None
    except ValueError:
        return _back('Precio, días y tasa deben ser números.')
    if price < 0 or days < 1 or (rate is not None and rate <= 0):
        return _back('Precio, días y tasa deben ser valores positivos.')
    values['plan_price_usd'] = ('%.2f' % price).rstrip('0').rstrip('.')
    values['plan_days'] = str(days)
    values['bs_rate'] = '' if rate is None else str(rate)

    for key, max_len in PAYMENT_TEXT_FIELDS.items():
        values[key] = (form.get(key) or '').strip()[:max_len]

    db.set_settings(values)
    return _back('Ajustes guardados.')


@admin_bp.route('/payments/<int:payment_id>/<action>', methods=['POST'])
@admin_required
def review_payment(payment_id, action):
    if action not in ('approve', 'reject'):
        return _back('Acción inválida.')
    days = int(db.get_settings()['plan_days'] or 30)
    changed = db.review_payment(payment_id, action == 'approve', current_user()['id'], days)
    if not changed:
        return _back('Ese pago ya fue revisado.')
    return _back('Pago aprobado: plan activado.' if action == 'approve' else 'Pago rechazado.')


@admin_bp.route('/users/<int:user_id>/plan', methods=['POST'])
@admin_required
def user_plan(user_id):
    action = request.form.get('action')
    if db.get_user_by_id(user_id) is None:
        return _back('Usuario no encontrado.')
    if action == 'grant':
        days = int(db.get_settings()['plan_days'] or 30)
        db.grant_plan(user_id, days)
        return _back(f'Plan activado {days} días.')
    if action == 'revoke':
        db.revoke_plan(user_id)
        return _back('Plan quitado.')
    return _back('Acción inválida.')
