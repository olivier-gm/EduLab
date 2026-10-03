"""Manifest, worker público y recursos de instalación sin sesión."""
import json
import struct
import db
from app import app


def test_pwa_public_resources_and_icons(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'pwa.db'))
    db.init_db()
    with app.test_client() as client:
        manifest = client.get('/static/manifest.webmanifest')
        assert manifest.status_code == 200
        data = json.loads(manifest.data)
        assert data['scope'] == '/' and data['display'] == 'standalone'
        for icon in data['icons']:
            response = client.get(icon['src'])
            assert response.status_code == 200
            width, height = struct.unpack('>II', response.data[16:24])
            assert icon['sizes'] == f'{width}x{height}'
        worker = client.get('/sw.js')
        assert worker.status_code == 200 and worker.mimetype == 'application/javascript'
        assert worker.headers['Cache-Control'] == 'no-cache'
        assert client.get('/static/offline.html').status_code == 200
        assert '/static/manifest.webmanifest' in client.get('/').get_data(as_text=True)
