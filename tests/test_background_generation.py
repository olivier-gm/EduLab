# -*- coding: utf-8 -*-
"""La generación corre en segundo plano: la petición termina al instante (ningún proxy la corta por
lenta que sea la IA), la página de espera consulta el avance y un cupo nunca se pierde ni se devuelve dos veces.
Estas pruebas usan el hilo real (GENERATION_INLINE desactivado)."""
import os
import threading
import time
from datetime import timedelta

import pytest

import db
import jobs
from IA import GenerationError
from title_check import TitleVerdict

app_module = pytest.importorskip('app')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    app_module.app.config['TESTING'] = True
    with app_module.app.app_context():
        uid = db.create_user('u@x.com', 'U')
        c = app_module.app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = uid
        c.uid = uid
        yield c


@pytest.fixture
def background(monkeypatch):
    """Hilos reales; al terminar la prueba se espera a los que sigan vivos."""
    monkeypatch.setitem(app_module.app.config, 'GENERATION_INLINE', False)
    yield
    for thread in threading.enumerate():
        if thread.name.startswith(('generation-', 'heartbeat-')):
            thread.join(timeout=15)


@pytest.fixture
def fake_document(monkeypatch):
    created = []

    def fill(docx_output, *args, **kwargs):
        os.makedirs(os.path.dirname(docx_output), exist_ok=True)
        open(docx_output, 'wb').write(b'x')
        open(docx_output[:-5] + '.pdf', 'wb').write(b'x')
        created.append(docx_output)
    monkeypatch.setattr(app_module.Document_process, 'fill_placeholders', staticmethod(fill))
    yield fill
    for path in created:
        for ext in ('.docx', '.pdf'):
            if os.path.exists(path[:-5] + ext):
                os.remove(path[:-5] + ext)


@pytest.fixture
def ia(monkeypatch):
    monkeypatch.setattr(app_module, 'check_title', lambda *a, **k: TitleVerdict(True))


def wait_for(token, *statuses, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        job = db.get_job(token)
        if job['status'] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f'El trabajo no llegó a {statuses}: {dict(db.get_job(token))}')


def wait_stage(token, stage, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if db.get_job(token)['stage'] == stage:
            return
        time.sleep(0.05)
    raise AssertionError(f'Nunca llegó a la etapa {stage}: {dict(db.get_job(token))}')


def start(client, **extra):
    data = {'title': 'EL PUMA', 'global-mode': 'ia'}
    data.update(extra)
    return client.post('/process_form', data=data)


def token_of(response):
    assert response.status_code == 302 and '/generating/' in response.location, response.location
    return response.location.rstrip('/').rsplit('/', 1)[1]


def give_credits(client, credits):
    db.set_settings({'plans_public_enabled': '1'})
    conn = db.get_db()
    conn.execute('UPDATE users SET credits = ? WHERE id = ?', (credits, client.uid))
    conn.commit()


def credits(client):
    return db.get_user_by_id(client.uid)['credits']


# ── La petición no espera a la IA ────────────────────────────────────

def test_la_peticion_termina_al_instante_aunque_la_ia_tarde(client, background, fake_document, ia, monkeypatch):
    gate = threading.Event()

    def slow_essay(*args, **kwargs):
        assert gate.wait(20)
        return 'Contenido educativo del puma.'
    monkeypatch.setattr(app_module, 'generate_essay_content', slow_essay)

    began = time.time()
    response = start(client)
    assert time.time() - began < 3                        # antes aquí se esperaban los minutos de la IA
    token = token_of(response)

    wait_stage(token, 'content')
    status = client.get(f'/generating/{token}/status')
    assert status.json['status'] == 'running' and status.json['stage'] == 'content'
    assert status.headers['Cache-Control'] == 'no-store'
    assert 'Generando tu documento' in client.get(f'/generating/{token}').get_data(as_text=True)
    assert not db.list_documents()                        # todavía no hay documento

    gate.set()
    wait_for(token, 'done')
    assert client.get(f'/generating/{token}/status').json['status'] == 'done'
    finish = client.get(f'/generating/{token}/finish')
    assert finish.status_code == 302 and '/choose_file/' in finish.location
    assert 'Tu documento está listo' in client.get(finish.location).get_data(as_text=True)
    assert len(db.list_documents()) == 1


def test_con_la_pagina_de_espera_abierta_al_terminar_redirige_sola(client, background, fake_document, ia, monkeypatch):
    monkeypatch.setattr(app_module, 'generate_essay_content', lambda *a, **k: 'Contenido.')
    token = token_of(start(client))
    wait_for(token, 'done')
    page = client.get(f'/generating/{token}')              # sin JavaScript: la recarga lleva a la descarga
    assert page.status_code == 302 and page.location.endswith(f'/generating/{token}/finish')


# ── Errores y cupo ───────────────────────────────────────────────────

def test_si_falla_se_muestra_el_motivo_y_se_devuelve_el_cupo(client, background, fake_document, ia, monkeypatch):
    give_credits(client, 2)

    def fail(*args, **kwargs):
        raise GenerationError('quota', 'La IA alcanzó su límite de uso.')
    monkeypatch.setattr(app_module, 'generate_essay_content', fail)
    token = token_of(start(client))
    job = wait_for(token, 'error')
    assert 'No se pudo generar el desarrollo' in job['message'] and 'límite de uso' in job['message']
    assert credits(client) == 2                            # se reservó 1 y se devolvió
    finish = client.get(f'/generating/{token}/finish', follow_redirects=True)
    html = finish.get_data(as_text=True)
    assert finish.request.path == '/form' and 'límite de uso' in html


def test_un_error_inesperado_tambien_devuelve_el_cupo(client, background, fake_document, ia, monkeypatch):
    give_credits(client, 1)

    def boom(*args, **kwargs):
        raise RuntimeError('algo no previsto')
    monkeypatch.setattr(app_module, 'generate_essay_content', boom)
    token = token_of(start(client))
    job = wait_for(token, 'error')
    assert 'error inesperado' in job['message'] and credits(client) == 1


def test_una_generacion_exitosa_consume_el_cupo_una_sola_vez(client, background, fake_document, ia, monkeypatch):
    give_credits(client, 2)
    monkeypatch.setattr(app_module, 'generate_essay_content', lambda *a, **k: 'Contenido.')
    wait_for(token_of(start(client)), 'done')
    assert credits(client) == 1


def test_solo_se_genera_un_documento_a_la_vez_por_usuario(client, background, fake_document, ia, monkeypatch):
    give_credits(client, 3)
    gate = threading.Event()
    monkeypatch.setattr(app_module, 'generate_essay_content', lambda *a, **k: (gate.wait(20), 'Contenido.')[1])
    first = token_of(start(client))
    wait_stage(first, 'content')
    second = token_of(start(client))
    assert second == first                                 # el segundo envío vuelve al avance del que corre
    assert credits(client) == 2                            # y no reservó otro cupo
    gate.set()
    wait_for(first, 'done')
    assert credits(client) == 2 and len(db.list_documents()) == 1


def test_sin_lugar_para_otra_generacion_avisa_y_no_cobra(client, background, fake_document, ia, monkeypatch):
    give_credits(client, 2)
    monkeypatch.setattr(jobs, '_slots', threading.BoundedSemaphore(0))
    response = start(client)
    assert response.status_code == 302 and response.location.endswith('/form')
    assert 'muchos documentos generándose' in client.get('/form').get_data(as_text=True)
    assert credits(client) == 2


# ── Trabajos abandonados ─────────────────────────────────────────────

def _age(token, seconds):
    old = (db._utcnow() - timedelta(seconds=seconds)).strftime(db.DATETIME_FMT)
    conn = db.get_db()
    conn.execute('UPDATE generation_jobs SET updated_at = ? WHERE token = ?', (old, token))
    conn.commit()


def test_un_trabajo_que_dejo_de_latir_se_cierra_y_devuelve_el_cupo_una_sola_vez(client):
    give_credits(client, 0)                                # el cupo ya se reservó (quedó en 0)
    token = db.create_job(client.uid, 'report', {'source': 'recharge'})
    _age(token, db.JOB_STALE_SECONDS + 5)
    assert db.reap_stale_jobs() == 1
    job = db.get_job(token)
    assert job['status'] == 'error' and 'se interrumpió' in job['message']
    assert credits(client) == 1
    assert db.reap_stale_jobs() == 0 and credits(client) == 1          # no se devuelve dos veces
    assert db.complete_job(token, 'x', []) is False                    # y un hilo tardío ya no lo reabre


def test_un_trabajo_vivo_no_se_cierra(client):
    token = db.create_job(client.uid, 'report', {'source': 'recharge'})
    _age(token, db.JOB_STALE_SECONDS - 60)
    assert db.reap_stale_jobs() == 0 and db.get_job(token)['status'] == 'running'


def test_la_pagina_de_espera_de_un_trabajo_abandonado_lleva_al_error(client):
    give_credits(client, 0)
    token = db.create_job(client.uid, 'report', {'source': 'recharge'})
    _age(token, db.JOB_STALE_SECONDS + 5)
    assert client.get(f'/generating/{token}/status').json['status'] == 'error'
    page = client.get(f'/generating/{token}/finish', follow_redirects=True)
    assert 'se interrumpió' in page.get_data(as_text=True) and credits(client) == 1


def test_el_latido_mantiene_vivo_el_trabajo(client, monkeypatch):
    monkeypatch.setattr(jobs, 'HEARTBEAT_SECONDS', 0.05)
    token = db.create_job(client.uid, 'report', {'source': 'recharge'})
    _age(token, db.JOB_STALE_SECONDS + 5)
    stop = threading.Event()
    thread = threading.Thread(target=jobs._heartbeat, args=(app_module.app, token, stop), name='heartbeat-test')
    thread.start()
    time.sleep(0.4)
    stop.set()
    thread.join(timeout=5)
    assert db.reap_stale_jobs() == 0 and db.get_job(token)['status'] == 'running'


def test_los_trabajos_terminados_se_borran_a_los_dos_dias(client):
    done = db.create_job(client.uid, 'report', {})
    db.complete_job(done, 'x', [])
    recent = db.create_job(client.uid, 'report', {})
    _age(done, db.JOB_KEEP_HOURS * 3600 + 60)
    db.purge_old_jobs()
    assert db.get_job(done) is None and db.get_job(recent) is not None


# ── Acceso y avisos ──────────────────────────────────────────────────

def test_el_avance_de_un_trabajo_solo_lo_ve_su_dueno(client, tmp_path):
    token = db.create_job(client.uid, 'report', {})
    other = db.create_user('otro@x.com', 'Otro')
    stranger = app_module.app.test_client()
    with stranger.session_transaction() as sess:
        sess['user_id'] = other
    for url in (f'/generating/{token}', f'/generating/{token}/status', f'/generating/{token}/finish'):
        assert stranger.get(url).status_code == 404
    assert client.get(f'/generating/{token}/status').status_code == 200
    anonymous = app_module.app.test_client()
    assert anonymous.get(f'/generating/{token}/status').status_code in (302, 401)
    assert client.get('/generating/no-existe/status').status_code == 404


def test_los_avisos_del_documento_se_muestran_una_sola_vez(client, fake_document):
    app_module.app.config['GENERATION_INLINE'] = True
    response = client.post('/process_form', data={'title': 'Un titulo', 'global-mode': 'standard'}, follow_redirects=True)
    assert 'solo la portada' in response.get_data(as_text=True)
    token = db.get_db().execute('SELECT token FROM generation_jobs').fetchone()['token']
    again = client.get(f'/generating/{token}/finish', follow_redirects=True)
    assert 'Tu documento está listo' in again.get_data(as_text=True) and 'solo la portada' not in again.get_data(as_text=True)


def test_el_hilo_de_fondo_respeta_el_tope_de_terminos_del_plan(client, background, fake_document, monkeypatch):
    """Sin petición HTTP el tope sale de la reserva de cupo del trabajo: Pro (300) sí, Premium (100) no."""
    db.set_settings({'plans_public_enabled': '1'})
    monkeypatch.setattr(app_module, 'check_glossary', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'generate_glossary', lambda *a, **k: [
        {'term': 'Término', 'definition': 'Definición breve.', 'reference': ''}])
    db.grant_plan(client.uid, 30, 'pro')
    wait_for(token_of(start(client, document_kind='glossary', glossary_count='250', title='Biologia celular')), 'done')
    # Premium: 100 términos como máximo
    conn = db.get_db()
    conn.execute("UPDATE generation_jobs SET status = 'done'")
    conn.execute("UPDATE users SET plan = 'premium' WHERE id = ?", (client.uid,))
    conn.commit()
    token = token_of(start(client, document_kind='glossary', glossary_count='250', title='Biologia celular'))
    job = wait_for(token, 'error')
    assert 'entre 1 y 100' in job['message']
