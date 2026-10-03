# -*- coding: utf-8 -*-
"""Mis informes: vencimiento a 24 h, acceso solo del dueño y limpieza automática."""
import os
import time
from datetime import timedelta

import pytest

import db
import retention

app_module = pytest.importorskip('app')

STEM = 'zz_test_informe'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    app_module.app.config['TESTING'] = True
    os.makedirs('output', exist_ok=True)
    with app_module.app.app_context():
        yield app_module.app.test_client()
    for name in os.listdir('output'):
        if name.startswith('zz_test_'):
            os.remove(os.path.join('output', name))


def make_user(email='a@x.com'):
    return db.create_user(email, 'Test')


def login(client, uid):
    with client.session_transaction() as sess:
        sess['user_id'] = uid


def make_files(stem, docx=True, pdf=True):
    for ext, wanted in (('.docx', docx), ('.pdf', pdf)):
        if wanted:
            open(os.path.join('output', stem + ext), 'wb').write(b'contenido')


def set_expiry(delta):
    """Mueve el vencimiento de todos los documentos: delta positivo = en el futuro."""
    stamp = (db._utcnow() + delta).strftime(db.DATETIME_FMT)
    db.get_db().execute('UPDATE documents SET expires_at = ?', (stamp,))
    db.get_db().commit()


# ── Plazo de 24 horas ─────────────────────────────────────────────────

def test_el_plazo_de_conservacion_es_24_horas():
    assert db.FILE_RETENTION_HOURS == 24
    assert db.SETTING_DEFAULTS['file_retention_hours'] == '24'


def test_documento_nuevo_vence_en_24_horas(client):
    uid = make_user()
    db.record_document(uid, 'Titulo', 'uni', 0, file_stem=STEM)
    doc = db.list_user_documents(uid)[0]
    remaining = db.plan_expiry.__globals__['datetime'].strptime(doc['expires_at'], db.DATETIME_FMT) - db._utcnow()
    assert timedelta(hours=23, minutes=59) < remaining <= timedelta(hours=24)


@pytest.mark.parametrize('delta,expected', [
    (timedelta(hours=23, minutes=59), '23 horas'),
    (timedelta(hours=3, minutes=5), '3 horas'),
    (timedelta(hours=2, minutes=1), '2 horas'),
    (timedelta(hours=1, minutes=30), '1 hora'),
    (timedelta(minutes=59), 'menos de una hora'),
    (timedelta(minutes=2), 'menos de una hora'),
    (timedelta(hours=49), '2 días y 1 hora'),
    (timedelta(hours=24), '1 día'),
])
def test_time_left_se_expresa_por_horas(delta, expected):
    stamp = (db._utcnow() + delta + timedelta(seconds=30)).strftime(db.DATETIME_FMT)
    assert db.time_left(stamp)[0] == expected


def test_time_left_fraccion_y_vencido():
    soon = (db._utcnow() + timedelta(hours=3, minutes=5)).strftime(db.DATETIME_FMT)
    assert 0 < db.time_left(soon)[1] < 0.2
    assert db.time_left((db._utcnow() - timedelta(minutes=1)).strftime(db.DATETIME_FMT))[0] == 'vencido'
    assert db.time_left(None) == ('vencido', 0.0)


# ── Página Mis informes ───────────────────────────────────────────────


def test_pagination_keeps_older_active_documents_accessible(client, monkeypatch):
    uid = make_user()
    stems = [f'zz_test_page_{i}' for i in range(51)]
    for index, stem in enumerate(stems):
        db.record_document(uid, f'Documento de página {index}', 'uni', 0, file_stem=stem)
    monkeypatch.setattr(app_module.storage, 'stems_available', lambda: {stem: ('docx',) for stem in stems})
    login(client, uid)
    first = client.get('/my_documents').get_data(as_text=True)
    assert 'Documento de página 50' in first and 'Documento de página 0<' not in first and 'Siguiente' in first
    second = client.get('/my_documents?page=2').get_data(as_text=True)
    assert 'Documento de página 0<' in second and 'Anterior' in second and 'Siguiente' not in second
    assert len(db.list_user_documents(uid, limit=50, offset=50)) == 1

def test_lista_solo_mis_informes_vigentes(client):
    a, b = make_user('a@x.com'), make_user('b@x.com')
    make_files(STEM)
    make_files(STEM + '_b')
    db.record_document(a, 'Informe de A', 'uni', 0, file_stem=STEM)
    db.record_document(b, 'Informe de B', 'uni', 0, file_stem=STEM + '_b')
    login(client, a)
    html = client.get('/my_documents').get_data(as_text=True)
    assert 'Informe de A' in html and 'Vence en' in html and 'horas' in html
    assert 'Informe de B' not in html


def test_informe_vencido_no_aparece(client):
    uid = make_user()
    make_files(STEM)
    db.record_document(uid, 'Ya vencido', 'uni', 0, file_stem=STEM)
    set_expiry(timedelta(hours=-1))
    login(client, uid)
    html = client.get('/my_documents').get_data(as_text=True)
    assert 'Ya vencido' not in html and 'Todavía no tienes informes' in html


def test_informe_sin_archivo_no_aparece(client):
    uid = make_user()
    db.record_document(uid, 'Sin archivo', 'uni', 0, file_stem=STEM)   # nunca se creó el .docx
    login(client, uid)
    assert 'Sin archivo' not in client.get('/my_documents').get_data(as_text=True)


def test_pdf_ausente_solo_muestra_word(client):
    uid = make_user()
    make_files(STEM, pdf=False)
    db.record_document(uid, 'Solo word', 'uni', 0, file_stem=STEM)
    login(client, uid)
    html = client.get('/my_documents').get_data(as_text=True)
    assert 'filetype' not in html and '/docx' in html and '/pdf' not in html
    assert 'Compartir Word' in html and 'Compartir PDF' not in html


def test_each_saved_document_shares_signed_downloads_with_original_expiry(client):
    import re
    uid = make_user()
    make_files(STEM)
    db.record_document(uid, 'Compartible', 'uni', 0, file_stem=STEM)
    login(client, uid)
    html = client.get('/my_documents').get_data(as_text=True)
    links = re.findall(r'data-share-url="http://localhost([^\"]+)"', html)
    assert len(links) == 2 and 'Compartir Word' in html and 'Compartir PDF' in html
    with client.session_transaction() as sess:
        sess.clear()
    for link in links:
        assert client.get(link).status_code == 200
    set_expiry(timedelta(minutes=-1))
    for link in links:
        assert client.get(link).status_code == 410


def test_requiere_iniciar_sesion(client):
    assert client.get('/my_documents').status_code == 302


# ── Descarga ──────────────────────────────────────────────────────────

def test_el_dueno_puede_descargar(client):
    uid = make_user()
    make_files(STEM)
    db.record_document(uid, 'Mi informe', 'uni', 0, file_stem=STEM)
    doc_id = db.list_user_documents(uid)[0]['id']
    login(client, uid)
    resp = client.get(f'/my_documents/{doc_id}/docx')
    assert resp.status_code == 200 and resp.data == b'contenido'
    assert 'Mi informe.docx' in resp.headers['Content-Disposition']


def test_otro_usuario_no_puede_descargar(client):
    a, b = make_user('a@x.com'), make_user('b@x.com')
    make_files(STEM)
    db.record_document(a, 'De A', 'uni', 0, file_stem=STEM)
    doc_id = db.list_user_documents(a)[0]['id']
    login(client, b)
    resp = client.get(f'/my_documents/{doc_id}/docx')
    assert resp.status_code == 302 and '/my_documents' in resp.headers['Location']


def test_no_se_descarga_un_informe_vencido(client):
    uid = make_user()
    make_files(STEM)
    db.record_document(uid, 'Vencido', 'uni', 0, file_stem=STEM)
    doc_id = db.list_user_documents(uid)[0]['id']
    set_expiry(timedelta(minutes=-1))
    login(client, uid)
    assert client.get(f'/my_documents/{doc_id}/docx').status_code == 302


def test_tipo_de_archivo_invalido(client):
    uid = make_user()
    make_files(STEM)
    db.record_document(uid, 'X', 'uni', 0, file_stem=STEM)
    doc_id = db.list_user_documents(uid)[0]['id']
    login(client, uid)
    assert client.get(f'/my_documents/{doc_id}/exe').status_code == 302


# ── Limpieza automática ───────────────────────────────────────────────

def test_limpieza_borra_solo_lo_vencido(client):
    uid = make_user()
    make_files(STEM + '_viejo')
    make_files(STEM + '_vigente')
    db.record_document(uid, 'Viejo', 'uni', 0, file_stem=STEM + '_viejo')
    db.record_document(uid, 'Vigente', 'uni', 0, file_stem=STEM + '_vigente')
    db.get_db().execute("UPDATE documents SET expires_at = ? WHERE title = 'Viejo'",
                        ((db._utcnow() - timedelta(minutes=5)).strftime(db.DATETIME_FMT),))
    db.get_db().commit()

    assert retention.cleanup_expired_files() >= 1
    assert not os.path.exists(f'output/{STEM}_viejo.docx') and not os.path.exists(f'output/{STEM}_viejo.pdf')
    assert os.path.exists(f'output/{STEM}_vigente.docx') and os.path.exists(f'output/{STEM}_vigente.pdf')


def test_limpieza_no_repite_trabajo_sobre_lo_ya_borrado(client):
    uid = make_user()
    make_files(STEM)
    db.record_document(uid, 'Viejo', 'uni', 0, file_stem=STEM)
    set_expiry(timedelta(minutes=-5))
    assert retention.cleanup_expired_files() >= 1
    assert retention.cleanup_expired_files() == 0


def test_limpieza_borra_archivos_sueltos_viejos_pero_no_los_recientes(client):
    make_files('zz_test_suelto_viejo')
    make_files('zz_test_suelto_nuevo')
    old = time.time() - (db.FILE_RETENTION_HOURS + 1) * 3600
    for ext in ('.docx', '.pdf'):
        os.utime(f'output/zz_test_suelto_viejo{ext}', (old, old))
    retention.cleanup_expired_files()
    assert not os.path.exists('output/zz_test_suelto_viejo.docx')
    assert os.path.exists('output/zz_test_suelto_nuevo.docx')


def test_limpieza_no_toca_archivos_vigentes_aunque_sean_viejos_en_disco(client):
    uid = make_user()
    make_files(STEM)
    db.record_document(uid, 'Vigente', 'uni', 0, file_stem=STEM)
    old = time.time() - (db.FILE_RETENTION_HOURS + 5) * 3600
    os.utime(f'output/{STEM}.docx', (old, old))
    retention.cleanup_expired_files()
    assert os.path.exists(f'output/{STEM}.docx')


def test_la_limpieza_no_arranca_bajo_pytest():
    # app.py no debe lanzar el hilo durante las pruebas (borraría output/).
    assert retention._started is False


def test_admin_retention_preserves_old_expiry_and_shared_links(client, monkeypatch):
    from datetime import datetime
    now = datetime(2026, 10, 1, 12)
    monkeypatch.setattr(db, '_utcnow', lambda: now)
    uid = make_user()
    login(client, uid)
    assert client.post('/admin/retention-settings').status_code in (302, 403)
    db.get_db().execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
    db.get_db().commit()
    db.record_document(uid, 'Antes', 'uni', 0, file_stem=STEM)
    old_expiry = db.get_document_by_filename(STEM)['expires_at']
    client.get('/admin/')
    with client.session_transaction() as sess:
        csrf = sess['ai_csrf_token']
        sess['file_generated'] = True
    assert client.post('/admin/retention-settings', data={
        'csrf_token': 'incorrecto', 'file_retention_hours': '72'}).status_code == 302
    assert db.get_retention_hours() == 24
    for value in ('0', '-1', '1.5', '8761', 'no'):
        client.post('/admin/retention-settings', data={'csrf_token': csrf, 'file_retention_hours': value})
        assert db.get_retention_hours() == 24
    client.post('/admin/retention-settings', data={'csrf_token': csrf, 'file_retention_hours': '72'})
    assert db.get_retention_hours() == 72
    assert db.get_document_by_filename(STEM)['expires_at'] == old_expiry
    db.record_document(uid, 'Después', 'uni', 0, file_stem=STEM + '_nuevo')
    doc = db.get_document_by_filename(STEM + '_nuevo')
    assert doc['expires_at'] == '2026-10-04 12:00:00'
    db.get_db().execute('UPDATE documents SET created_at = ?', (now.strftime(db.DATETIME_FMT),))
    db.get_db().commit()
    make_files(STEM)
    make_files(STEM + '_nuevo')
    old_token = app_module._share_serializer().dumps(STEM)
    new_token = app_module._share_serializer().dumps(STEM + '_nuevo')
    now += timedelta(hours=25)
    client.post('/admin/retention-settings', data={'csrf_token': csrf, 'file_retention_hours': '1'})
    assert client.get(f'/s/{old_token}/docx').status_code == 410
    assert client.get(f'/s/{new_token}/docx').status_code == 200
    html = client.get('/my_documents').get_data(as_text=True)
    assert '1 día y 23 horas' in html and 'data-created=' in html
    assert abs(db.time_left(doc['expires_at'], '2026-10-01 12:00:00')[1] - 47 / 72) < .001
    assert '1 día y 23 horas' in client.get('/choose_file/' + STEM + '_nuevo').get_data(as_text=True)
    now += timedelta(hours=48)
    assert client.get(f'/s/{new_token}/docx').status_code == 410


def test_dashboard_stats_zero_days_timezone_and_approved_revenue(client, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(db, '_utcnow', lambda: datetime(2026, 10, 1, 12))
    stats, daily = db.get_dashboard_stats(7)
    assert len(daily) == 7 and stats['period_documents'] == 0
    uid = make_user()
    db.record_document(uid, 'IA', 'uni', 1200, mode='ai')
    db.record_document(uid, 'Manual', 'uni', 0)
    conn = db.get_db()
    conn.execute("UPDATE documents SET created_at = '2026-10-01 02:00:00'")
    conn.execute("UPDATE users SET created_at = '2026-09-29 12:00:00'")
    conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
    conn.commit()
    approved = db.create_payment(uid, 'binance', 'ok', 5)
    db.review_payment(approved, True, uid, 30)
    db.create_payment(uid, 'binance', 'pending', 9)
    stats, daily = db.get_dashboard_stats(7)
    assert daily[-2]['documents'] == 2 and daily[-1]['documents'] == 0
    assert stats['period_tokens'] == stats['average_tokens'] == 1200
    assert stats['period_ai'] == stats['period_manual'] == stats['active_users'] == 1
    assert stats['premium'] == stats['pending'] == stats['new_users'] == 1
    assert stats['revenue'] == 5
    login(client, uid)
    html = client.get('/admin/?days=7').get_data(as_text=True)
    assert 'Tokens por día' in html and 'Documentos por día' in html and 'mode-donut' in html
    assert 'Últimos 7 días' in html and 'csrf_token' in html
