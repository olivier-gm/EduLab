# plans.py
"""Plan de pago: reglas de acceso a la generación de documentos, página de
planes y registro de pagos.

Reglas (un solo plan por ahora):
  - Admin o usuario con plan ACTIVO (premium y no vencido): sin límites.
  - Usuario sin plan: lo que el admin haya configurado en el panel, por
    separado para documentos con IA y manuales (activado/desactivado y
    cuántos documentos por usuario). Límite vacío = sin límite.
  - Si intenta algo que no tiene permitido, se le redirige a /plans.

El pago es manual: el usuario paga por Binance o Pago Móvil, reporta su
referencia y un admin la aprueba en /admin, lo que activa el plan.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path

import requests
from flask import Blueprint, current_app, render_template, request, redirect, url_for

import db
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
    if user['is_admin'] or db.has_active_plan(user):
        return True, None

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
    unlimited = bool(user['is_admin']) or db.has_active_plan(user)
    summary = {}
    for mode in MODES:
        ok, reason = generation_access(user, mode, settings)
        summary[mode] = {
            'ok': ok,
            'reason': reason,
            'used': db.count_user_documents(user['id'], mode),
            'limit': None if unlimited else parse_limit(settings[f'free_{mode}_limit']),
        }
    return summary


def redirect_to_plans(reason):
    return redirect(url_for('plans.plans', reason=reason))


def _reason_message(reason, summary):
    if not reason:
        return None
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
    summary = access_summary(user, settings)

    price = float(settings['plan_price_usd'] or 0)
    price_bs, rate_label = _bolivar_quote(settings)

    payments = db.get_user_payments(user['id'])
    return render_template(
        'plans.html',
        settings=settings,
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
    )


@plans_bp.route('/plans/pay', methods=['POST'])
@login_required
def pay():
    user = current_user()
    settings = db.get_settings()
    method = request.form.get('method', '')
    reference = request.form.get('reference', '').strip()

    def fail(message):
        return redirect(url_for('plans.plans', error=message))

    if method not in PAY_METHODS:
        return fail('Elige un método de pago.')
    if not REFERENCE_RE.match(reference):
        return fail('La referencia debe tener entre 4 y 40 caracteres (letras, números o guiones).')
    if any(p['status'] == 'pending' for p in db.get_user_payments(user['id'])):
        return fail('Ya tienes un pago en revisión. Espera a que se apruebe.')
    if db.reference_in_use(method, reference):
        return fail('Esa referencia ya fue reportada.')

    db.create_payment(user['id'], method, reference, float(settings['plan_price_usd'] or 0))
    return redirect(url_for('plans.plans', sent=1))
