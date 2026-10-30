import json
import re
from xml.etree import ElementTree

import pytest

import db
import seo
from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'seo.db'))
    db.init_db()
    with app.test_client() as client:
        yield client


@pytest.mark.parametrize('path', ['/', '/privacy', '/terms'])
def test_public_pages_have_stable_canonicals_and_remain_indexable(client, path):
    response = client.get(path + '?utm_source=prueba', headers={'Host': 'otro-dominio.example'})
    assert response.status_code == 200
    assert 'X-Robots-Tag' not in response.headers
    page = response.get_data(as_text=True)
    assert f'<link rel="canonical" href="https://edulab.wiki{path}">' in page
    assert '<meta name="robots" content="noindex' not in page


@pytest.mark.parametrize('path', ['/login', '/register', '/forgot-password', '/reset-password',
                                  '/register/verify', '/form', '/my_documents', '/admin/', '/plans', '/no-existe'])
def test_account_and_app_pages_are_not_indexable(client, path):
    response = client.get(path)
    assert response.headers['X-Robots-Tag'] == 'noindex, follow'
    if path in ('/login', '/register', '/forgot-password'):
        assert '<meta name="robots" content="noindex, follow">' in response.get_data(as_text=True)


def test_home_metadata_and_brand_schema(client):
    page = client.get('/').get_data(as_text=True)
    assert f'<title>{seo.TITLE}</title>' in page
    assert f'<meta name="description" content="{seo.DESCRIPTION}">' in page
    assert f'<meta property="og:title" content="{seo.TITLE}">' in page
    assert '<meta property="og:url" content="https://edulab.wiki/">' in page
    scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    graph = json.loads(scripts[0])['@graph']
    assert graph[0]['@type'] == 'WebSite' and graph[0]['name'] == 'EduLab'
    assert graph[0]['url'] == 'https://edulab.wiki/'
    assert graph[1]['@id'] == graph[0]['publisher']['@id']
    assert graph[1]['logo'] == 'https://edulab.wiki/static/img/icon.png'


def test_robots_allows_google_to_read_noindex_and_advertises_sitemap(client):
    response = client.get('/robots.txt')
    assert response.status_code == 200 and response.mimetype == 'text/plain'
    text = response.get_data(as_text=True)
    assert 'Sitemap: https://edulab.wiki/sitemap.xml' in text
    assert 'Disallow: /login' not in text and 'Allow: /' in text


def test_sitemap_lists_only_canonical_public_pages(client):
    response = client.get('/sitemap.xml', headers={'Host': 'otro-dominio.example'})
    assert response.status_code == 200 and response.mimetype == 'application/xml'
    root = ElementTree.fromstring(response.data)
    urls = [node.text for node in root.findall('{*}url/{*}loc')]
    assert urls == ['https://edulab.wiki/', 'https://edulab.wiki/privacy', 'https://edulab.wiki/terms']
    for url in urls:
        assert client.get(url).status_code == 200


def test_brand_icon_can_still_be_indexed(client):
    response = client.get('/static/img/icon.png')
    assert response.status_code == 200 and 'X-Robots-Tag' not in response.headers
