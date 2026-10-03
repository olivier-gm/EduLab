"""Correos de verificación por Gmail, sin dependencias adicionales."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from flask import current_app


def mail_configured():
    return bool(os.getenv('GMAIL_USER', '').strip() and os.getenv('GMAIL_APP_PASSWORD', '').strip())


def send_verification(email, name, code, purpose='register'):
    sender = os.environ['GMAIL_USER'].strip()
    password = os.environ['GMAIL_APP_PASSWORD'].replace(' ', '').strip()
    message = EmailMessage()
    reset = purpose == 'reset'
    message['Subject'] = 'Recupera tu contraseña · EduLab' if reset else 'Tu código de verificación · EduLab'
    message['From'] = formataddr(('EduLab', sender))
    message['To'] = email
    message.set_content(f'Hola, {name}.\n\nTu código de EduLab es: {code}\n'
                        + ('Vence en 10 minutos. Úsalo para elegir una nueva contraseña.\n' if reset else
                           'Vence en 10 minutos. Tu cuenta solo se creará al verificarlo.\n') +
                        'Si no hiciste esta solicitud, ignora este correo. No compartas el código.')
    message.add_alternative(current_app.jinja_env.get_template('emails/verification.html').render(name=name, code=code, reset=reset), subtype='html')
    logo = Path(current_app.static_folder) / 'img' / 'icon.png'
    message.get_payload()[-1].add_related(logo.read_bytes(), maintype='image', subtype='png',
                                        cid='<edulab-logo>', disposition='inline', filename='edulab.png')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15, context=ssl.create_default_context()) as smtp:
        smtp.login(sender, password)
        refused = smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
