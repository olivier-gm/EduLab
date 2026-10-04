"""Correos de verificación por Gmail, sin dependencias adicionales."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from flask import current_app


def mail_configured():
    return bool(os.getenv('GMAIL_USER', '').strip() and os.getenv('GMAIL_APP_PASSWORD', '').strip())


def send_verification(email, name, code, purpose='register'):
    sender = os.environ['GMAIL_USER'].strip()
    password = os.environ['GMAIL_APP_PASSWORD'].replace(' ', '').strip()
    message = EmailMessage()
    reset = purpose == 'reset'
    message['Subject'] = (f'{code} es tu código para recuperar tu contraseña · EduLab' if reset else
                          f'{code} es tu código de verificación · EduLab')
    message['From'] = formataddr(('EduLab', sender))
    message['To'] = email
    message['Date'] = formatdate(usegmt=True)
    message['Message-ID'] = make_msgid(domain=sender.rsplit('@', 1)[-1])
    message.set_content(f'Tu código de EduLab es: {code}. Vence en 10 minutos.\n\nHola, {name}.\n\n'
                        + ('Vence en 10 minutos. Úsalo para elegir una nueva contraseña.\n' if reset else
                           'Vence en 10 minutos. Tu cuenta solo se creará al verificarlo.\n') +
                        'Si no hiciste esta solicitud, ignora este correo. No compartas el código.')
    logo_url = os.getenv('MAIL_LOGO_URL', '').strip() or 'https://edulab.wiki/static/img/icon-192.png'
    message.add_alternative(current_app.jinja_env.get_template('emails/verification.html').render(
        name=name, code=code, reset=reset, logo_url=logo_url), subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15, context=ssl.create_default_context()) as smtp:
        smtp.login(sender, password)
        refused = smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
