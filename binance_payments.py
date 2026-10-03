"""Verificación de Binance Pay. Las credenciales y las llamadas son del servidor."""
import json
import os
import time
from decimal import Decimal, InvalidOperation

import requests

import db

DEFAULT_URL = 'https://binance-bmgnb2e5facwaqf5.canadacentral-01.azurewebsites.net'
MESSAGES = {
    'VERIFIED': 'Pago confirmado. Tu plan ya está activo.',
    'NOT_FOUND': 'No encontramos el pago en las últimas 3 horas. Revisa el ID de la transacción de Binance Pay.',
    'AMOUNT_MISMATCH': 'El importe recibido no alcanza el monto del plan elegido.',
    'ALREADY_CLAIMED': 'Ese pago ya se utilizó para otra compra.',
    'PENDING_SYNC': 'Binance está sincronizando el pago. Volveremos a comprobarlo en unos segundos.',
    'BINANCE_API_UNAVAILABLE': 'Binance no responde por el momento. Tu referencia se conserva para reintentar.',
    'BINANCE_NOT_CONFIGURED': 'La verificación de Binance necesita configuración. Tu referencia se conserva.',
    'CONFIG_ERROR': 'La verificación de Binance no está disponible. Tu referencia se conserva.',
    'VERIFYING': 'Estamos comprobando tu pago con Binance.',
    'SERVICE_ERROR': 'No pudimos confirmar el pago todavía. Tu referencia se conserva para reintentar.',
    'USER_CANCELLED': 'Referencia retirada para corregir el ID de pago.',
}
REJECTIONS = {'NOT_FOUND', 'AMOUNT_MISMATCH', 'ALREADY_CLAIMED'}


def configured():
    return bool(os.environ.get('BINANCE_VERIFY_TOKEN', '').strip())


def payment_result(payment):
    status = payment['provider_status']
    pending = payment['status'] == 'pending'
    return {'status': payment['status'], 'provider_status': status,
            'verified': payment['status'] == 'approved',
            'retryable': pending and status not in ('CONFIG_ERROR', 'BINANCE_NOT_CONFIGURED'),
            'retry_after': max(6, int(payment['verify_after'] - time.time()) + 1) if pending else 0,
            'message': ('Todavía no encontramos el pago. Estamos esperando la sincronización; revisa que el ID sea correcto.'
                        if pending and status == 'NOT_FOUND' else MESSAGES.get(status, 'Tu pago está pendiente de revisión.'))}


def verify_payment(payment_id):
    payment = db.begin_payment_verification(payment_id)
    if not payment:
        return payment_result(db.get_payment(payment_id))
    provider_status, outcome = 'SERVICE_ERROR', 'pending'
    try:
        token = os.environ.get('BINANCE_VERIFY_TOKEN', '').strip()
        if not token:
            provider_status = 'CONFIG_ERROR'
        else:
            config = json.loads(payment['plan_snapshot']) if payment['plan_snapshot'] else {}
            amount = Decimal(str(config.get('price', payment['amount_usd'])))
            if not amount.is_finite() or amount < 0:
                raise ValueError('Precio inválido')
            response = requests.post(
                os.environ.get('BINANCE_VERIFY_URL', DEFAULT_URL).rstrip('/') + '/v1/payments/verify',
                headers={'Authorization': 'Bearer ' + token},
                json={'paymentCode': payment['reference'], 'expectedAmount': format(amount, '.2f'),
                      'asset': 'USDT', 'orderReference': payment['order_reference'], 'maxAgeMinutes': 1440},
                timeout=(5, 90), allow_redirects=False,
            )
            if response.status_code == 200:
                result = response.json()
                if isinstance(result, dict):
                    status = result.get('status')
                    if status == 'VERIFIED' and result.get('verified') is True:
                        provider_status, outcome = 'VERIFIED', 'approved'
                    elif status in REJECTIONS and result.get('verified') is not True and result.get('retryable') is not True:
                        provider_status, outcome = status, 'rejected'
                    elif result.get('retryable') is True and result.get('verified') is not True:
                        provider_status = status if status in MESSAGES else 'SERVICE_ERROR'
                    elif status == 'BINANCE_NOT_CONFIGURED':
                        provider_status = status
            elif response.status_code in (401, 403, 422):
                provider_status = 'CONFIG_ERROR'
    except (requests.RequestException, ValueError, TypeError, InvalidOperation, KeyError):
        # Un timeout puede ocurrir después de reclamar el pago: se conserva el
        # pedido y se reintenta con la misma referencia idempotente.
        pass
    db.finish_payment_verification(payment_id, payment['verify_attempt'], provider_status, outcome)
    return payment_result(db.get_payment(payment_id))
