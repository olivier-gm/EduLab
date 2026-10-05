import json

import pytest

import db
import landing
from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    app.config['TESTING'] = True
    with app.app_context():
        yield app.test_client()


def login_admin(client):
    uid = db.create_user('preview@example.invalid', 'Admin')
    db.get_db().execute('UPDATE users SET is_admin = 1 WHERE id = ?', (uid,))
    db.get_db().commit()
    with client.session_transaction() as session:
        session['user_id'] = uid
    client.get('/admin/')
    with client.session_transaction() as session:
        return session['ai_csrf_token']


def test_default_preview_has_three_matching_logos():
    institutions, _ = landing.preview(db.SETTING_DEFAULTS, now=0)
    assert len(institutions) == 3
    assert any(item['filename'].endswith('.webp') for item in institutions)
    assert all(item['id'] in item['filename'] for item in institutions)


def test_title_stays_fixed_in_bucket_and_changes_at_boundary():
    settings = {**db.SETTING_DEFAULTS, 'landing_titles': 'Título uno\nTítulo dos\nTítulo tres'}
    titles = [landing.preview(settings, now=n * landing.TITLE_PERIOD)[1] for n in range(3)]
    assert len(set(titles)) == 3
    assert landing.preview(settings, now=landing.TITLE_PERIOD - 1)[1] == titles[0]
    assert landing.preview(settings, now=landing.TITLE_PERIOD)[1] == titles[1]


def test_admin_can_select_single_or_no_university_and_edit_titles(client):
    token = login_admin(client)
    selected = 'universidad_simon_bolivar'
    response = client.post('/admin/landing-settings', data={
        'csrf_token': token, 'landing_universities': selected, 'landing_titles': 'El cuerpo humano'})
    assert response.status_code == 302
    assert json.loads(db.get_settings()['landing_universities']) == [selected]
    page = client.get('/').get_data(as_text=True)
    assert 'EL CUERPO HUMANO' in page and 'UNIVERSIDAD SIMÓN BOLÍVAR' in page
    client.post('/admin/landing-settings', data={'csrf_token': token, 'landing_titles': 'El cuerpo humano'})
    assert 'TU INSTITUCIÓN EDUCATIVA' in client.get('/').get_data(as_text=True)


@pytest.mark.parametrize('changes', [
    {'csrf_token': 'incorrect'}, {'landing_universities': '../secret'},
    {'landing_titles': ''}, {'landing_titles': 'x' * 121}])
def test_invalid_admin_input_does_not_save(client, changes):
    token = login_admin(client)
    before = db.get_settings()
    data = {'csrf_token': token, 'landing_titles': 'Título válido'}
    data.update(changes)
    client.post('/admin/landing-settings', data=data)
    assert db.get_settings() == before


def test_non_admin_cannot_change_preview(client):
    before = db.get_settings()
    response = client.post('/admin/landing-settings', data={'landing_titles': 'No permitido'})
    assert response.status_code in (302, 403)
    assert db.get_settings() == before
