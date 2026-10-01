# -*- coding: utf-8 -*-
"""Almacenamiento de informes: carpeta local y R2 (API S3, con un cliente falso)."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

import db
import retention
import storage

app_module = pytest.importorskip('app')


class FakeS3:
    """Lo mínimo de boto3 que usa storage.py, guardando todo en memoria."""

    def __init__(self):
        self.objects = {}          # key -> {'body', 'content_type', 'modified'}
        self.fail_uploads = False

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return {}

    def upload_file(self, path, Bucket, Key, ExtraArgs=None):
        if self.fail_uploads:
            raise ClientError({'Error': {'Code': '500', 'Message': 'boom'}}, 'PutObject')
        self.objects[Key] = {'body': open(path, 'rb').read(),
                             'content_type': (ExtraArgs or {}).get('ContentType'),
                             'modified': datetime.now(timezone.utc)}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return (f"https://r2.example/{Params['Bucket']}/{Params['Key']}"
                f"?exp={ExpiresIn}&cd={Params['ResponseContentDisposition']}")

    def get_paginator(self, name):
        outer = self

        class Pager:
            def paginate(self, Bucket, Prefix=''):
                yield {'Contents': [{'Key': k, 'LastModified': v['modified']}
                                    for k, v in outer.objects.items() if k.startswith(Prefix)]}
        return Pager()


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'OUTPUT_DIR', str(tmp_path / 'output'))
    os.makedirs(storage.OUTPUT_DIR)
    return tmp_path


@pytest.fixture
def r2(local, monkeypatch):
    """Almacenamiento remoto activo con un cliente S3 falso."""
    monkeypatch.setenv('R2_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_ACCESS_KEY_ID', 'clave')
    monkeypatch.setenv('R2_SECRET_ACCESS_KEY', 'secreto')
    monkeypatch.setenv('R2_BUCKET', 'informes')
    fake = FakeS3()
    monkeypatch.setattr(storage, '_client', fake)
    yield fake
    storage.reset_client()


def make_local(stem, docx=True, pdf=True):
    for ext, wanted in (('docx', docx), ('pdf', pdf)):
        if wanted:
            open(storage.local_path(stem, ext), 'wb').write(f'{ext}-{stem}'.encode())


# ── Configuración ─────────────────────────────────────────────────────

def test_sin_credenciales_es_local(local):
    assert not storage.is_remote()


@pytest.mark.parametrize('missing', ['R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET'])
def test_con_credenciales_incompletas_sigue_siendo_local(r2, monkeypatch, missing):
    monkeypatch.setenv(missing, '')
    assert not storage.is_remote()


def test_otro_proveedor_s3_solo_cambia_el_endpoint(r2, monkeypatch):
    monkeypatch.setenv('R2_ACCOUNT_ID', '')
    assert not storage.is_remote()
    monkeypatch.setenv('S3_ENDPOINT_URL', 'https://nyc3.digitaloceanspaces.com')
    assert storage.is_remote()


# ── Modo local ────────────────────────────────────────────────────────

def test_local_publicar_no_mueve_nada(local):
    make_local('doc')
    storage.publish('doc')
    assert storage.exists('doc', 'docx') and storage.exists('doc', 'pdf')


def test_publicar_sin_docx_falla(local):
    with pytest.raises(storage.StorageError):
        storage.publish('no_existe')


def test_nombre_unico_local(local):
    make_local('informe')
    stem = storage.unique_stem('informe')
    assert stem != 'informe' and stem.startswith('informe_')
    assert storage.unique_stem('libre') == 'libre'


# ── R2 ────────────────────────────────────────────────────────────────

def test_r2_publicar_sube_y_borra_las_copias_locales(r2):
    make_local('doc')
    storage.publish('doc')
    assert set(r2.objects) == {'doc.docx', 'doc.pdf'}
    assert r2.objects['doc.docx']['content_type'].endswith('wordprocessingml.document')
    assert r2.objects['doc.pdf']['content_type'] == 'application/pdf'
    assert not os.path.exists(storage.local_path('doc', 'docx'))
    assert storage.exists('doc', 'docx') and storage.exists('doc', 'pdf')


def test_r2_sin_pdf_sube_solo_el_word(r2):
    make_local('doc', pdf=False)
    storage.publish('doc')
    assert set(r2.objects) == {'doc.docx'} and not storage.exists('doc', 'pdf')


def test_r2_si_la_subida_falla_conserva_lo_local_y_avisa(r2):
    make_local('doc')
    r2.fail_uploads = True
    with pytest.raises(storage.StorageError):
        storage.publish('doc')
    assert os.path.exists(storage.local_path('doc', 'docx'))     # no se pierde el documento


def test_r2_prefijo_de_claves(r2, monkeypatch):
    monkeypatch.setenv('R2_PREFIX', 'informes/')
    make_local('doc')
    storage.publish('doc')
    assert set(r2.objects) == {'informes/doc.docx', 'informes/doc.pdf'}
    assert storage.stems_available() == {'doc': {'docx', 'pdf'}}


def test_r2_descarga_redirige_a_una_url_firmada_con_nombre(r2):
    make_local('doc')
    storage.publish('doc')
    with app_module.app.test_request_context('/'):
        resp = storage.serve('doc', 'docx', download_name='Mi informe ñandú.docx')
    assert resp.status_code == 302
    url = resp.headers['Location']
    assert url.startswith('https://r2.example/informes/doc.docx') and 'exp=300' in url
    assert 'filename*=UTF-8' in url and '%C3%B1' in url          # ñ codificada


def test_r2_descargar_algo_inexistente(r2):
    with app_module.app.test_request_context('/'):
        with pytest.raises(FileNotFoundError):
            storage.serve('fantasma', 'docx')


def test_r2_nombre_unico_consulta_el_bucket(r2):
    make_local('informe')
    storage.publish('informe')                  # ahora solo está en el bucket
    assert storage.unique_stem('informe') != 'informe'


def test_r2_borrar(r2):
    make_local('doc')
    storage.publish('doc')
    storage.delete('doc')
    assert r2.objects == {}


def test_r2_limpieza_de_restos_locales(r2):
    make_local('resto')
    old = time.time() - 7200
    for ext in ('docx', 'pdf'):
        os.utime(storage.local_path('resto', ext), (old, old))
    make_local('reciente')
    assert storage.local_scratch_cleanup(3600) == 2
    assert os.path.exists(storage.local_path('reciente', 'docx'))


# ── Limpieza automática con R2 ────────────────────────────────────────

@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    with app_module.app.app_context():
        yield


def test_r2_la_limpieza_borra_lo_vencido_y_respeta_lo_vigente(r2, database):
    uid = db.create_user('a@x.com', 'A')
    for stem in ('viejo', 'vigente'):
        make_local(stem)
        storage.publish(stem)
        db.record_document(uid, stem, 'uni', 0, file_stem=stem)
    db.get_db().execute('UPDATE documents SET expires_at = ? WHERE title = ?',
                        ((db._utcnow() - timedelta(minutes=5)).strftime(db.DATETIME_FMT), 'viejo'))
    db.get_db().commit()

    assert retention.cleanup_expired_files() >= 1
    assert set(r2.objects) == {'vigente.docx', 'vigente.pdf'}


def test_r2_la_limpieza_borra_huerfanos_viejos(r2, database):
    make_local('huerfano')
    storage.publish('huerfano')
    r2.objects['huerfano.docx']['modified'] = (
        datetime.now(timezone.utc) - timedelta(hours=db.FILE_RETENTION_HOURS + 1))
    retention.cleanup_expired_files()
    assert 'huerfano.docx' not in r2.objects and 'huerfano.pdf' in r2.objects


# ── La app con R2 ─────────────────────────────────────────────────────

@pytest.fixture
def client(database):
    app_module.app.config['TESTING'] = True
    uid = db.create_user('u@x.com', 'U')
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = uid
    return c, uid


def test_app_mis_informes_y_descarga_desde_r2(r2, client):
    c, uid = client
    make_local('zz_r2_informe')
    storage.publish('zz_r2_informe')
    db.record_document(uid, 'Informe en R2', 'uni', 0, file_stem='zz_r2_informe')

    html = c.get('/my_documents').get_data(as_text=True)
    assert 'Informe en R2' in html and '/pdf' in html

    doc_id = db.list_user_documents(uid)[0]['id']
    resp = c.get(f'/my_documents/{doc_id}/docx')
    assert resp.status_code == 302 and resp.headers['Location'].startswith('https://r2.example/')


def test_app_enlace_compartido_redirige_a_r2(r2, client):
    c, uid = client
    make_local('zz_r2_compartido')
    storage.publish('zz_r2_compartido')
    db.record_document(uid, 'Compartido', 'uni', 0, file_stem='zz_r2_compartido')
    token = app_module._share_serializer().dumps('zz_r2_compartido')
    resp = app_module.app.test_client().get(f'/s/{token}/pdf')      # sin sesión
    assert resp.status_code == 302 and 'zz_r2_compartido.pdf' in resp.headers['Location']


def test_app_informe_que_ya_no_esta_en_r2_no_aparece(r2, client):
    c, uid = client
    db.record_document(uid, 'Perdido', 'uni', 0, file_stem='zz_perdido')
    assert 'Perdido' not in c.get('/my_documents').get_data(as_text=True)


# ── Flujo completo de generación con R2 ───────────────────────────────

@pytest.fixture
def fake_document(monkeypatch):
    """Evita LibreOffice: escribe un .docx y un .pdf mínimos donde se pide."""
    written = []

    def fill(docx_output, *args, **kwargs):
        os.makedirs(os.path.dirname(docx_output), exist_ok=True)
        open(docx_output, 'wb').write(b'word')
        open(docx_output[:-5] + '.pdf', 'wb').write(b'pdf')
        written.append(docx_output)

    monkeypatch.setattr(app_module.Document_process, 'fill_placeholders', staticmethod(fill))
    yield written
    for path in written:
        for ext in ('.docx', '.pdf'):
            if os.path.exists(path[:-5] + ext):
                os.remove(path[:-5] + ext)


def post_manual(c, title='Zz Informe R2'):
    return c.post('/process_form', follow_redirects=True,
                  data={'title': title, 'global-mode': 'standard', 'body': 'Contenido del usuario'})


def test_app_generar_sube_a_r2_y_se_puede_descargar(r2, client, fake_document, monkeypatch):
    monkeypatch.setattr(storage, 'OUTPUT_DIR', 'output')       # donde escribe la app
    c, uid = client
    html = post_manual(c).get_data(as_text=True)

    assert 'Descargar Word' in html
    stem = db.list_user_documents(uid)[0]['file_stem']
    assert {f'{stem}.docx', f'{stem}.pdf'} <= set(r2.objects)
    assert not os.path.exists(f'output/{stem}.docx')            # el servidor no guarda nada

    resp = c.get(f'/download_file/{stem}/docx')
    assert resp.status_code == 302
    from urllib.parse import unquote
    assert f'{stem}.docx' in unquote(resp.headers['Location'])


def test_app_si_r2_falla_no_registra_el_documento(r2, client, fake_document, monkeypatch):
    monkeypatch.setattr(storage, 'OUTPUT_DIR', 'output')
    c, uid = client
    r2.fail_uploads = True
    html = post_manual(c).get_data(as_text=True)
    assert 'No se pudo guardar el documento' in html
    assert db.list_user_documents(uid) == []


def test_app_dos_usuarios_con_el_mismo_titulo_no_se_pisan(r2, client, fake_document, monkeypatch):
    monkeypatch.setattr(storage, 'OUTPUT_DIR', 'output')
    c, uid = client
    post_manual(c)
    other = db.create_user('otro@x.com', 'Otro')
    c2 = app_module.app.test_client()
    with c2.session_transaction() as sess:
        sess['user_id'] = other
    post_manual(c2)
    a = db.list_user_documents(uid)[0]['file_stem']
    b = db.list_user_documents(other)[0]['file_stem']
    assert a != b and {f'{a}.docx', f'{b}.docx'} <= set(r2.objects)
