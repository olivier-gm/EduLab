# admin.py
"""Panel admin: usuarios, tokens de Gemini, documentos, y la gestión de
planes de pago: reglas para usuarios sin plan, datos de cobro, aprobación
de pagos y activar/quitar el plan a mano."""

from flask import Blueprint, render_template, request, redirect, url_for

import db
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
    return render_template(
        'admin.html',
        stats=db.get_stats(),
        users=db.list_users(),
        documents=db.list_documents(limit=100),
        settings=db.get_settings(),
        payments=db.list_payments(limit=100),
        active_plan=db.has_active_plan,
        plan_expiry=db.plan_expiry,
        msg=request.args.get('msg'),
    )


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
