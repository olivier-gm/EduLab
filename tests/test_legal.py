"""Las páginas legales y sus enlaces deben ser públicos, incluso sin sesión."""
import pytest
import db
from app import app


@pytest.mark.parametrize('path,title', [('/privacy', 'Política de privacidad'), ('/terms', 'Términos y condiciones')])
def test_public_legal_pages(tmp_path, monkeypatch, path, title):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'legal.db'))
    db.init_db()
    with app.test_client() as client:
        response = client.get(path)
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert f'<h1>{title}</h1>' in page and 'EduLab' in page
        assert 'href="/privacy"' in page and 'href="/terms"' in page
        assert 'mailto:edulab.wiki@gmail.com' in page
        home = client.get('/').get_data(as_text=True)
        assert 'href="/privacy"' in home and 'href="/terms"' in home
