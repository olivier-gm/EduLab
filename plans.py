# plans.py
"""Recarga sin vencimiento, planes mensuales y pagos revisados por el admin.
Los planes mensuales tienen prioridad sobre el saldo de recarga.
"""

import re
import time
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache, wraps
from pathlib import Path

import requests
from flask import Blueprint, current_app, render_template, request, redirect, url_for, g, session, abort, jsonify

import db
import binance_payments
from auth import login_required, current_user

plans_bp = Blueprint('plans', __name__)

MODES = ('ai', 'manual')
MODE_LABELS = {'ai': 'con IA', 'manual': 'manuales'}
PAY_METHODS = {'binance': 'Binance Pay', 'pago_movil': 'Pago Móvil'}

REFERENCE_RE = re.compile(r'^[A-Za-z0-9\-_.]{4,40}$')


@lru_cache(maxsize=1)
def _bcv_rate(bucket):
    """Consulta acotada; guarda éxitos y fallos durante cinco minutos."""
    try:
        response = requests.get('https://ve.dolarapi.com/v1/dolares/oficial', timeout=5)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get('moneda') != 'USD' or data.get('fuente') != 'oficial':
            return None
        rate = Decimal(str(data['promedio']))
        effective = datetime.fromisoformat(data['fechaActualizacion']).date()
        today = datetime.now(timezone(timedelta(hours=-4))).date()
        if (rate.is_finite() and rate > 0 and effective is not None
                and 0 <= (today - effective).days <= 4):
            return rate, effective
    except (requests.RequestException, KeyError, TypeError, ValueError, InvalidOperation):
        pass
    return None


def _bolivar_quote(settings):
    quote = _bcv_rate(int(time.time() // 300))
    if quote:
        rate, effective = quote
        label = f'Tasa BCV: Bs. {rate:,.4f} por USD · Actualización: {effective:%d/%m/%Y} · DolarAPI'
    else:
        try:
            rate = Decimal(settings['bs_rate'] or '0')
        except InvalidOperation:
            return None, 'Tasa BCV no disponible. Consulta el monto antes de pagar.'
        if not rate.is_finite() or rate <= 0:
            return None, 'Tasa BCV no disponible. Consulta el monto antes de pagar.'
        label = f'Tasa manual de respaldo: Bs. {rate:,.4f} por USD (BCV no disponible)'
    amount = (Decimal(settings['plan_price_usd'] or '0') * rate).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP)
    return amount, label


def _payment_logos():
    """Logos locales opcionales, en orden SVG, PNG y WebP."""
    logos = {}
    for method, name in (('binance', 'binance'), ('pago_movil', 'bdv')):
        logos[method] = next(
            (f'img/payments/{name}.{ext}' for ext in ('svg', 'png', 'webp')
             if (Path(current_app.static_folder) / f'img/payments/{name}.{ext}').is_file()),
            None,
        )
    return logos


def parse_limit(raw):
    """'' → None (sin límite); '3' → 3. Cualquier cosa rara → None."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def generation_access(user, mode, settings=None):
    """(permitido, motivo) para generar un documento en `mode` ('ai'|'manual').

    El motivo es un código ('ai_disabled', 'manual_limit', ...) que /plans
    traduce a un mensaje.
    """
    if user is None:
        return False, 'login'
    if user['is_admin'] or getattr(g, 'generation_ticket', None):
        return True, None

    settings = settings or db.get_settings()
    if not db.public_plans_enabled(settings):
        return (True, None) if db.free_mode_used(user) < db.FREE_MODE_LIMIT else (False, 'free_mode_limit')
    state = db.billing_state(user)
    if state['source'] != 'free':
        return (True, None) if state['remaining'] else (False, 'monthly_limit')

    settings = settings or db.get_settings()
    if settings[f'free_{mode}_enabled'] != '1':
        return False, f'{mode}_disabled'

    limit = parse_limit(settings[f'free_{mode}_limit'])
    if limit is not None and db.count_user_documents(user['id'], mode) >= limit:
        return False, f'{mode}_limit'
    return True, None


def access_summary(user, settings=None):
    """Por modo: {'ok', 'reason', 'used', 'limit'} — para pintar la UI."""
    settings = settings or db.get_settings()
    state = db.billing_state(user)
    summary = {}
    for mode in MODES:
        ok, reason = generation_access(user, mode, settings)
        summary[mode] = {
            'ok': ok,
            'reason': reason,
            'used': state['used'] if state['source'] != 'free' else db.count_user_documents(user['id'], mode),
            'limit': None if user['is_admin'] or not db.public_plans_enabled(settings) else (user['credits'] if state['source'] == 'recharge' else state['limit'] if state['source'] != 'free' else parse_limit(settings[f'free_{mode}_limit'])),
        }
        if not user['is_admin'] and not db.public_plans_enabled(settings):
            summary[mode].update(used=db.free_mode_used(user), limit=db.FREE_MODE_LIMIT)
    return summary


def with_generation_quota(view):
    @wraps(view)
    def run(*args, **kwargs):
        user = current_user()
        if request.form.get('document_kind') == 'glossary' and not glossary_access(user):
            return redirect_to_plans('glossary_unavailable')
        if _wants_glossary_bibliography() and not glossary_bibliography_access(user):
            return redirect_to_plans('glossary_bibliography_unavailable')
        manual = request.endpoint == 'process_form_bach' or (
            request.form.get('document_kind') != 'glossary' and request.form.get('global-mode') == 'standard')
        allowed, reason = generation_access(user, 'manual' if manual else 'ai')
        if not allowed:
            return redirect_to_plans(reason)
        ticket = db.reserve_generation(user['id'], allow_free=db.billing_state(user)['source'] == 'free')
        if ticket is None:
            return redirect_to_plans('monthly_limit' if db.public_plans_enabled() else 'free_mode_limit')
        g.generation_ticket = ticket
        try:
            if request.form.get('document_kind') == 'glossary' and not glossary_access(user):
                return redirect_to_plans('glossary_unavailable')
            if _wants_glossary_bibliography() and not glossary_bibliography_access(user):
                return redirect_to_plans('glossary_bibliography_unavailable')
            return view(*args, **kwargs)
        finally:
            # 'handed_off': la generación quedó en un trabajo en segundo plano, que cierra el cupo
            # (lo consume al terminar o lo devuelve si falla); aquí ya no se toca.
            if not ticket.get('done') and not ticket.get('handed_off'):
                db.refund_generation(user['id'], ticket)
            g.pop('generation_ticket', None)
    return run


def glossary_access(user):
    ticket = getattr(g, 'generation_ticket', None)
    return bool(user and (user['is_admin'] or (ticket or db.billing_state(user))['source'] != 'recharge'))


def _wants_glossary_bibliography():
    return request.form.get('document_kind') == 'glossary' and 'incluir_bibliografia' in request.form


def glossary_bibliography_access(user):
    """La bibliografía con fuentes por término de los glosarios (una investigación web por cada
    tanda de 25 términos, la parte más cara) es solo del plan Pro. Admin y el modo sin planes
    públicos la tienen siempre."""
    if not user:
        return False
    if user['is_admin'] or not db.public_plans_enabled():
        return True
    return (getattr(g, 'generation_ticket', None) or db.billing_state(user))['source'] == 'pro'


def redirect_to_plans(reason):
    if reason == 'free_mode_limit':
        return redirect(url_for('show_form'))
    return redirect(url_for('plans.plans', reason=reason))


def _reason_message(reason, summary):
    if not reason:
        return None
    if reason == 'glossary_unavailable':
        return 'La recarga incluye informes en Word. Para crear glosarios, elige un plan mensual.'
    if reason == 'glossary_bibliography_unavailable':
        return 'La bibliografía por término de los glosarios es exclusiva del plan Pro. Puedes generar el glosario sin ella.'
    if reason == 'monthly_limit':
        return 'Alcanzaste el cupo de tu período mensual. Tu recarga permanece en pausa hasta que venza el plan.'
    if reason in ('ai_disabled', 'manual_disabled'):
        mode = reason.split('_')[0]
        return f'Los documentos {MODE_LABELS[mode]} están disponibles solo con el plan.'
    if reason in ('ai_limit', 'manual_limit'):
        mode = reason.split('_')[0]
        limit = summary[mode]['limit']
        return (f'Ya usaste tus {limit} documento(s) {MODE_LABELS[mode]} gratuitos. '
                'Activa el plan para seguir generando.')
    if reason == 'none':
        return 'Sin plan no puedes generar documentos por ahora.'
    return None


@plans_bp.route('/plans')
@login_required
def plans():
    user = current_user()
    settings = db.get_settings()
    if not user['is_admin'] and not db.public_plans_enabled(settings):
        return redirect(url_for('show_form'))
    summary = access_summary(user, settings)

    catalog = db.plan_catalog(settings)
    selected_id = request.args.get('plan', 'premium')
    if selected_id not in catalog or not catalog[selected_id]['enabled']:
        selected_id = next((key for key, value in catalog.items() if value['enabled']), None)
    selected = catalog.get(selected_id)
    price = float(selected['price']) if selected else 0
    price_bs, rate_label = _bolivar_quote({**settings, 'plan_price_usd': str(price)})

    payments = db.get_user_payments(user['id'])
    return render_template(
        'plans.html',
        settings=settings,
        catalog=catalog, selected=selected, billing=db.billing_state(user), credits=user['credits'],
        price=price,
        price_bs=price_bs,
        rate_label=rate_label,
        active=db.has_active_plan(user),
        expires_at=db.plan_expiry(user),
        summary=summary,
        payments=payments,
        has_pending=any(p['status'] == 'pending' for p in payments),
        pay_methods=PAY_METHODS,
        payment_logos=_payment_logos(),
        reason_message=_reason_message(request.args.get('reason'), summary),
        sent=request.args.get('sent') == '1',
        error=request.args.get('error'),
        payment_csrf_token=session.setdefault('payment_csrf_token', secrets.token_urlsafe(32)),
        payment_messages=binance_payments.MESSAGES,
    )


@plans_bp.route('/plans/pay', methods=['POST'])
@login_required
def pay():
    user = current_user()
    settings = db.get_settings()
    if not user['is_admin'] and not db.public_plans_enabled(settings):
        abort(403)
    _check_payment_csrf()
    method = request.form.get('method', '')
    reference = request.form.get('reference', '').strip()

    def fail(message):
        return redirect(url_for('plans.plans', error=message))

    plan_id = request.form.get('plan_id', 'premium')
    catalog = db.plan_catalog(settings)
    if plan_id not in catalog or not catalog[plan_id]['enabled']:
        return fail('Ese plan no está disponible para comprar.')

    if method not in PAY_METHODS:
        return fail('Elige un método de pago.')
    if not REFERENCE_RE.match(reference):
        return fail('La referencia debe tener entre 4 y 40 caracteres (letras, números o guiones).')
    if any(p['status'] == 'pending' for p in db.get_user_payments(user['id'])):
        return fail('Ya tienes un pago en revisión. Espera a que se apruebe.')
    if db.reference_in_use(method, reference):
        return fail('Esa referencia ya fue reportada.')

    try:
        payment_id = db.create_payment(user['id'], method, reference, float(catalog[plan_id]['price']), plan_id, snapshot=catalog[plan_id])
    except ValueError as exc:
        return fail(str(exc))
    if method == 'binance':
        result = binance_payments.verify_payment(payment_id)
        return redirect(url_for('plans.plans', plan=plan_id, error=result['message'] if result['status'] == 'rejected' else None,
                                sent=0 if result['status'] == 'rejected' else 1))
    return redirect(url_for('plans.plans', sent=1))


def _check_payment_csrf():
    token = session.get('payment_csrf_token')
    if not token or not hmac.compare_digest(token, request.form.get('csrf_token', '')):
        abort(400)


@plans_bp.route('/plans/payments/<int:payment_id>/verify', methods=['POST'])
@login_required
def verify_payment(payment_id):
    user = current_user()
    if not user['is_admin'] and not db.public_plans_enabled():
        abort(403)
    _check_payment_csrf()
    payment = db.get_payment(payment_id)
    if not payment or payment['user_id'] != user['id'] or payment['method'] != 'binance':
        abort(404)
    return jsonify(binance_payments.verify_payment(payment_id))


@plans_bp.route('/plans/payments/<int:payment_id>/correct', methods=['POST'])
@login_required
def correct_payment(payment_id):
    user = current_user()
    if not user['is_admin'] and not db.public_plans_enabled():
        abort(403)
    _check_payment_csrf()
    if not db.cancel_missing_payment(payment_id, user['id']):
        return redirect(url_for('plans.plans', error='Espera a que termine la comprobación. Solo puedes corregir un ID que Binance no haya encontrado.'))
    return redirect(url_for('plans.plans'))
