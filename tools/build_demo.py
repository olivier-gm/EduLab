# -*- coding: utf-8 -*-
"""Genera la versión estática (demo) de Gullieth para GitHub Pages.

Renderiza las plantillas Jinja REALES de la app con datos de ejemplo y las
convierte a HTML estático con rutas relativas (GitHub Pages sirve el sitio bajo
/nombre-del-repo/, así que nada puede empezar con "/"). El comportamiento del
servidor (generar, descargar, pagar, login, admin) lo simula static/js/demo.js.

Uso, desde la raíz de una copia de la rama principal:
    python <checkout_de_demo>/tools/build_demo.py <carpeta_de_salida> [<assets_desde>]

    <carpeta_de_salida>  dónde escribir el sitio (normalmente el checkout de la rama demo).
    <assets_desde>       carpeta con static/js/demo.js, static/css/demo.css, static/vendor/
                         y README.md (por defecto, la propia carpeta de salida, así que
                         regenerar la demo en su lugar conserva el simulador y las librerías).
"""
import datetime
import os
import re
import shutil
import sys

ROOT = os.getcwd()
sys.path.insert(0, ROOT)

import app as A                      # noqa: E402  (necesita el proyecto real)
from flask import render_template    # noqa: E402

OUT = os.path.abspath(sys.argv[1])
ASSETS_DIR = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else OUT

ROUTES = {
    '/': 'index.html', '/form': 'form.html', '/login': 'login.html',
    '/register': 'register.html', '/plans': 'plans.html', '/admin': 'admin.html',
    '/admin/': 'admin.html', '/process_form': 'download.html',
}

ACCESS = {m: {'ok': True, 'reason': None, 'used': 0, 'limit': None} for m in ('ai', 'manual')}
SETTINGS = {
    'free_ai_enabled': '1', 'free_ai_limit': '', 'free_manual_enabled': '1', 'free_manual_limit': '',
    'plan_name': 'Plan Premium', 'plan_price_usd': '5', 'plan_days': '30',
    'binance_email': 'demo@ejemplo.com', 'pm_bank': 'Banco de Venezuela (BDV) · 0102',
    'pm_phone': '0414-0000000', 'pm_id': 'V-00.000.000', 'pm_holder': 'Titular de ejemplo', 'bs_rate': '',
}


def sample_admin_context():
    now = datetime.datetime(2026, 9, 30, 12, 0)
    users = [
        {'id': 1, 'name': 'Admin Demo', 'email': 'admin@demo.com', 'is_admin': 1, 'google_id': None,
         'plan': 'premium', 'plan_expires_at': '2027-01-01 00:00:00', 'documents_count': 12,
         'tokens_used': 184230, 'created_at': '2026-08-01 10:12:00'},
        {'id': 2, 'name': 'María Rodríguez', 'email': 'maria@ejemplo.com', 'is_admin': 0, 'google_id': 'g-1',
         'plan': 'premium', 'plan_expires_at': '2026-10-28 00:00:00', 'documents_count': 7,
         'tokens_used': 96410, 'created_at': '2026-09-02 15:40:00'},
        {'id': 3, 'name': 'Juan García', 'email': 'juan@ejemplo.com', 'is_admin': 0, 'google_id': None,
         'plan': 'free', 'plan_expires_at': None, 'documents_count': 2,
         'tokens_used': 21500, 'created_at': '2026-09-20 09:05:00'},
    ]
    documents = [
        {'title': 'La Blockchain y sus Capas', 'user_name': 'María Rodríguez', 'user_email': 'maria@ejemplo.com',
         'doc_type': 'uni', 'tokens_used': 15230, 'created_at': '2026-09-29 18:22:00'},
        {'title': 'Psicología del Trading', 'user_name': 'Juan García', 'user_email': 'juan@ejemplo.com',
         'doc_type': 'uni', 'tokens_used': 0, 'created_at': '2026-09-28 11:03:00'},
    ]
    payments = [
        {'id': 1, 'user_name': 'Juan García', 'user_email': 'juan@ejemplo.com', 'method': 'pago_movil',
         'reference': '0012345678', 'amount_usd': 5.0, 'status': 'pending', 'created_at': '2026-09-30 08:30:00'},
        {'id': 2, 'user_name': 'María Rodríguez', 'user_email': 'maria@ejemplo.com', 'method': 'binance',
         'reference': 'BN99887766', 'amount_usd': 5.0, 'status': 'approved', 'created_at': '2026-09-28 20:11:00'},
    ]

    def active(u):
        return u['plan'] == 'premium'

    def expiry(u):
        return datetime.datetime.strptime(u['plan_expires_at'], '%Y-%m-%d %H:%M:%S')

    return dict(
        stats={'total_users': 3, 'total_documents': 21, 'total_tokens': 302140},
        users=users, documents=documents, payments=payments, settings=SETTINGS,
        active_plan=active, plan_expiry=expiry,
        msg='Esto es una demo: los cambios no se guardan.',
    )


def render_pages():
    with A.app.test_request_context('/'):
        return {
            'index.html': render_template('main_page.html'),
            'form.html': render_template('universitario.html', access=ACCESS),
            'plans.html': render_template(
                'plans.html', settings=SETTINGS, price=5.0, price_bs=None, active=False,
                expires_at=None, summary=ACCESS, payments=[], has_pending=False,
                pay_methods=A.plans.PAY_METHODS, reason_message=None, sent=False, error=None),
            'download.html': render_template(
                'download.html', filename='Mi documento', share_urls={'docx': 'X', 'pdf': 'X'}),
            'login.html': render_template('login.html', error=None, next='', google_enabled=False),
            'register.html': render_template('register.html', error=None, google_enabled=False),
            'admin.html': render_template('admin.html', **sample_admin_context()),
            '404.html': render_template('404.html'),
        }


def to_static(name, html):
    # Rutas relativas (el sitio vive bajo /<repo>/)
    html = re.sub(r'(["\'(])/static/', r'\1static/', html)
    html = re.sub(r'(href|action)="/#([^"]*)"', r'\1="index.html#\2"', html)

    def route(m):
        attr, target = m.group(1), m.group(2)
        clean = target.split('?')[0]
        if clean.startswith('/download_file/'):
            kind = clean.rsplit('/', 1)[-1]
            return '%s="#" data-dl="%s"' % (attr, kind)
        if clean in ROUTES:
            return '%s="%s"' % (attr, ROUTES[clean])
        return '%s="#"' % attr          # rutas de servidor sin equivalente estático
    html = re.sub(r'(href|action)="(/[^"]*)"', route, html)

    if name == 'download.html':
        html = html.replace('Se envía un enlace de descarga que funciona sin iniciar sesión y vence en 2 horas.',
                            'En la demo se comparte el enlace de esta página web.')
        html = html.replace("'). Descárgalo aquí, el enlace vence en 2 horas: '",
                            "'). Prueba la demo de Gullieth para generar informes académicos: '")
        html = re.sub(r'var urls = \{[^;]*\};',
                      "var DEMO_URL = new URL('index.html', location.href).href;\n"
                      "      var urls = { docx: DEMO_URL, pdf: DEMO_URL };", html)

    # Scripts de la demo: librerías propias (sin CDN) + simulador del servidor
    extra = ''
    if name == 'download.html':
        extra += ('<script src="static/vendor/docx.min.js"></script>\n'
                  '<script src="static/vendor/jspdf.umd.min.js"></script>\n')
    extra += '<script src="static/js/demo.js"></script>\n'
    html = html.replace('</body>', extra + '</body>', 1)
    html = html.replace('</head>', '<link rel="stylesheet" href="static/css/demo.css">\n</head>', 1)
    return html


def patch_builder(js):
    # Barra de progreso más rápida: en la demo no hay IA que esperar.
    js = js.replace('progress += (100 - progress) * 0.0083 + 0.06;',
                    'progress += (100 - progress) * 0.11 + 0.4;')
    js = js.replace("'/static/logos'", "'static/logos'")
    return js


def copy_tree(src, dst):
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, html in render_pages().items():
        with open(os.path.join(OUT, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(to_static(name, html))

    static = os.path.join(ROOT, 'static')
    dest = os.path.join(OUT, 'static')
    os.makedirs(os.path.join(dest, 'css'), exist_ok=True)
    os.makedirs(os.path.join(dest, 'js'), exist_ok=True)
    for css in ('base', 'landing', 'form', 'download', 'plans', 'auth', 'admin'):
        shutil.copy(os.path.join(static, 'css', css + '.css'), os.path.join(dest, 'css', css + '.css'))
    for js in ('ui.js',):
        shutil.copy(os.path.join(static, 'js', js), os.path.join(dest, 'js', js))
    with open(os.path.join(static, 'js', 'builder.js'), encoding='utf-8') as f:
        builder = patch_builder(f.read())
    with open(os.path.join(dest, 'js', 'builder.js'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(builder)
    for tree in ('img', 'logos', 'txt'):
        copy_tree(os.path.join(static, tree), os.path.join(dest, tree))
    for single in ('icono.jpg', 'image1.png', 'image2.png', '404.jpeg'):
        if os.path.isfile(os.path.join(static, single)):
            shutil.copy(os.path.join(static, single), os.path.join(dest, single))

    # Simulador + estilos + librerías (se conservan de <assets_desde>)
    for rel in ('js/demo.js', 'css/demo.css'):
        src, dst = os.path.join(ASSETS_DIR, 'static', rel), os.path.join(dest, rel)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy(src, dst)
    src_vendor, dst_vendor = os.path.join(ASSETS_DIR, 'static', 'vendor'), os.path.join(dest, 'vendor')
    if os.path.abspath(src_vendor) != os.path.abspath(dst_vendor):
        copy_tree(src_vendor, dst_vendor)
    open(os.path.join(OUT, '.nojekyll'), 'w').close()
    readme = os.path.join(ASSETS_DIR, 'README.md')
    if os.path.abspath(readme) != os.path.abspath(os.path.join(OUT, 'README.md')):
        shutil.copy(readme, os.path.join(OUT, 'README.md'))

    # Aviso de rutas absolutas que hayan quedado (rompen bajo /<repo>/)
    for name in os.listdir(OUT):
        if name.endswith('.html'):
            text = open(os.path.join(OUT, name), encoding='utf-8').read()
            left = set(re.findall(r'(?:href|src|action)="(/[^"]*)"', text))
            if left:
                print('AVISO', name, 'rutas absolutas:', sorted(left))
    print('demo generada en', OUT)


if __name__ == '__main__':
    main()
