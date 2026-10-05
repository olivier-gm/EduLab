# admin.py
"""Panel admin: usuarios, tokens de Gemini, documentos, y la gestión de
planes de pago: reglas para usuarios sin plan, datos de cobro, aprobación
de pagos y activar/quitar el plan a mano."""

import hmac
import os
import re
import secrets
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, session

import db
import landing
import json
import ai_provider
import IA
import binance_payments
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
    days = request.args.get('days', 30, type=int)
    if days not in (7, 30, 90):
        days = 30
    stats, daily = db.get_dashboard_stats(days)
    key_status = {
        prefix: 'Guardada en el panel' if values[f'{prefix}_api_key'] else (
            'Configurada en el servidor' if os.environ.get(env_name) else 'Sin configurar')
        for prefix, env_name in [('gemini', 'GEMINI_API_KEY'), ('openrouter', 'OPENROUTER_API_KEY')]
    }
    return render_template(
        'admin.html',
        stats=stats, daily=daily, days=days,
        chart_max_documents=max(1, max(row['documents'] for row in daily)),
        chart_max_tokens=max(1, max(row['tokens'] for row in daily)),
        users=db.list_users(),
        documents=db.list_documents(limit=100),
        settings=values,
        landing_universities=landing.universities(),
        landing_selected=landing.selected_ids(values),
        catalog=db.plan_catalog(values),
        gemini_default_model=IA.MODEL_NAME,
        key_status=key_status,
        ai_csrf_token=session.setdefault('ai_csrf_token', secrets.token_urlsafe(32)),
        payments=db.list_payments(limit=100),
        binance_configured=binance_payments.configured(),
        payment_messages=binance_payments.MESSAGES,
        active_plan=db.has_active_plan,
        plan_expiry=db.plan_expiry,
        msg=request.args.get('msg'),
    )


@admin_bp.route('/landing-settings', methods=['POST'])
@admin_required
def save_landing_settings():
    def back(message):
        return redirect(url_for('admin.dashboard', msg=message) + '#portada-muestra')
    token = session.get('ai_csrf_token')
    if not token or not hmac.compare_digest(token, request.form.get('csrf_token', '')):
        return back('Recarga el panel antes de guardar la portada de muestra.')
    selected = request.form.getlist('landing_universities')
    available = {item['id'] for item in landing.universities()}
    if any(value not in available for value in selected):
        return back('Selecciona universidades que tengan un logo disponible.')
    titles = list(dict.fromkeys(line.strip() for line in request.form.get('landing_titles', '').splitlines() if line.strip()))
    if not 1 <= len(titles) <= 30 or any(len(title) > 120 for title in titles):
        return back('Añade entre 1 y 30 títulos, de máximo 120 caracteres cada uno.')
    db.set_settings({'landing_universities': json.dumps(list(dict.fromkeys(selected))),
                     'landing_titles': '\n'.join(titles)})
    return back('Portada de muestra guardada. Los cambios se ven al recargar la página de inicio.')


@admin_bp.route('/catalog-settings', methods=['POST'])
@admin_required
def save_catalog_settings():
    token = session.get('ai_csrf_token')
    if not token or not hmac.compare_digest(token, request.form.get('csrf_token', '')):
        return _back('Recarga el panel antes de guardar los planes.')
    values = {}
    try:
        for key in db.PLAN_IDS:
            price = Decimal(request.form.get(f'{key}_price', '').replace(',', '.'))
            if not price.is_finite() or not 0 < price <= 100000:
                raise ValueError()
            values[f'{key}_price'] = str(price.quantize(Decimal('.01')))
            name = request.form.get(f'{key}_name', '').strip()
            if not name or len(name) > 60:
                raise ValueError()
            values[f'{key}_name'] = name
            values[f'{key}_enabled'] = '1' if request.form.get(f'{key}_enabled') else '0'
            for field, maximum in [('limit', 1000000), ('terms', 10000), ('hours', 8760)]:
                raw = request.form.get(f'{key}_{field}', '')
                if not raw.isascii() or not raw.isdigit() or len(raw) > 7 or not 1 <= int(raw) <= maximum:
                    raise ValueError()
                values[f'{key}_{field}'] = str(int(raw))
            benefits = request.form.get(f'{key}_benefits', '').strip()
            if len(benefits) > 4000:
                raise ValueError()
            values[f'{key}_benefits'] = benefits
    except (ValueError, InvalidOperation):
        return _back('Revisa los planes: nombre, precio positivo, generaciones, términos y conservación entre 1 y 8760 horas.')
    db.set_settings(values)
    return _back('Planes guardados. Desactivar oculta la compra; no quita planes ni saldos existentes. Los documentos anteriores mantienen su vencimiento.')


@admin_bp.route('/retention-settings', methods=['POST'])
@admin_required
def save_retention_settings():
    token = session.get('ai_csrf_token')
    message = 'La sesión del formulario venció. Recarga el panel.'
    if token and hmac.compare_digest(token, request.form.get('csrf_token', '')):
        raw = request.form.get('file_retention_hours', '').strip()
        if len(raw) <= 4 and raw.isascii() and raw.isdigit() and 1 <= int(raw) <= 8760:
            db.set_settings({'file_retention_hours': str(int(raw))})
            message = 'Conservación actualizada. Solo afecta a los documentos creados a partir de ahora.'
        else:
            message = 'Indica un número entero entre 1 y 8760 horas (un año).'
    return redirect(url_for('admin.dashboard', msg=message) + '#retencion')


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
        light = (request.form.get(f'{prefix}_light_model') or '').strip()
        if light and (len(light) > 160 or not light.startswith(expected) or not re.fullmatch(r'[A-Za-z0-9._/-]+', light)):
            return back(f'Escribe el ID del modelo ligero correcto: {expected}… (o déjalo vacío para usar el principal).')
        values[f'{prefix}_light_model'] = light
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
    values['fallback_enabled'] = '1' if request.form.get('fallback_enabled') else '0'
    for prefix in ('gemini', 'openrouter'):
        values[f'{prefix}_search_enabled'] = '1' if request.form.get(f'{prefix}_search_enabled') else '0'
    proposed = {**db.get_settings(), **values}
    try:
        _, _, active_key = ai_provider.configuration(values=proposed)
        if values['fallback_enabled'] == '1':
            _, _, other_key = ai_provider.configuration(values=proposed, provider=ai_provider._other_provider(proposed))
    except IA.GenerationError as exc:
        return back(exc.user_message)
    if not active_key:
        return back('Añade la clave API del proveedor que quieres activar antes de guardar.')
    if values['fallback_enabled'] == '1' and not other_key:
        return back('Para activar el fallback necesitas la clave API de los dos proveedores. '
                    'Añade la del otro proveedor o desactiva el fallback.')
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
    token = session.get('ai_csrf_token')
    if not token or not hmac.compare_digest(token, request.form.get('csrf_token', '')):
        return _back('Recarga el panel e inténtalo otra vez.')
    if action not in ('approve', 'reject'):
        return _back('Acción inválida.')
    payment = db.get_payment(payment_id)
    if not payment:
        return _back('Pago no encontrado.')
    if payment['method'] == 'binance':
        if action != 'approve':
            return _back('Los pagos Binance se resuelven mediante la verificación de la API.')
        return _back(binance_payments.verify_payment(payment_id)['message'])
    days = int(db.get_settings()['plan_days'] or 30)
    changed = db.review_payment(payment_id, action == 'approve', current_user()['id'], days)
    if not changed:
        return _back('Ese pago ya fue revisado.')
    return _back('Pago aprobado: plan activado.' if action == 'approve' else 'Pago rechazado.')


@admin_bp.route('/plans-visibility', methods=['POST'])
@admin_required
def plans_visibility():
    token = session.get('ai_csrf_token')
    if not token or not hmac.compare_digest(token, request.form.get('csrf_token', '')):
        return _back('Recarga el panel e inténtalo otra vez.')
    enabled = request.form.get('plans_public_enabled') == '1'
    db.set_settings({'plans_public_enabled': '1' if enabled else '0'})
    return _back('Planes visibles para todos.' if enabled else 'Modo gratuito activado. Solo los admins ven los planes.')


@admin_bp.route('/users/<int:user_id>/plan', methods=['POST'])
@admin_required
def user_plan(user_id):
    action = request.form.get('action')
    if db.get_user_by_id(user_id) is None:
        return _back('Usuario no encontrado.')
    if action == 'grant':
        days = int(db.get_settings()['plan_days'] or 30)
        plan_id = request.form.get('plan_id', 'premium')
        if plan_id not in db.PLAN_IDS:
            return _back('Plan inválido.')
        if plan_id == 'recharge':
            db.get_db().execute('UPDATE users SET credits = credits + ? WHERE id = ?',
                                (db.plan_catalog()['recharge']['limit'], user_id))
            db.get_db().commit()
            return _back('Recarga añadida. Su saldo no vence.')
        db.grant_plan(user_id, days, plan_id)
        return _back(f'Plan activado {days} días.')
    if action == 'revoke':
        db.revoke_plan(user_id)
        return _back('Plan quitado.')
    return _back('Acción inválida.')
