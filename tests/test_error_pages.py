import db
from app import app


def test_error_pages_have_correct_status_branding_and_no_internal_details(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'errors.db'))
    db.init_db()
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    client = app.test_client()
    missing = client.get('/pagina-que-no-existe')
    assert missing.status_code == 404
    assert 'Esta página no está en el índice' in missing.get_data(as_text=True)

    def fail():
        raise RuntimeError('detalle interno que no debe mostrarse')
    monkeypatch.setitem(app.view_functions, 'welcome', fail)
    failed = client.get('/')
    assert failed.status_code == 500
    assert 'Volver a intentar' in failed.get_data(as_text=True)
    for response in (missing, failed):
        html = response.get_data(as_text=True)
        assert '/static/img/icon.png' in html and 'og:image' in html
        assert 'detalle interno' not in html and 'HTTrack' not in html
        assert 'Volver al inicio' in html
