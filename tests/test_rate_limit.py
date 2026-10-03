import time
import pytest
import rate_limit
import db
import auth
from app import app


@pytest.fixture
def rate_limit_app(tmp_path, monkeypatch):
    # Asegurar que el rate limiting está habilitado y con límites conocidos para el test
    monkeypatch.setenv('RATE_LIMIT_ENABLED', '1')
    monkeypatch.setenv('RATE_LIMIT_GLOBAL', '5')
    
    # Reseteamos el store de rate_limit antes de cada test
    rate_limit._reset()
    
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'rate_limit_test.db'))
    db.init_db()
    
    with app.test_client() as client, app.app_context():
        yield client


def test_global_rate_limit(rate_limit_app, monkeypatch):
    # El límite global para la IP es de 5 req/min (por el fixture)
    # Hacemos 5 peticiones exitosas
    for _ in range(5):
        resp = rate_limit_app.get('/')
        assert resp.status_code == 200
        
    # La número 6 debe ser bloqueada
    resp = rate_limit_app.get('/')
    assert resp.status_code == 429
    assert 'Retry-After' in resp.headers
    assert 'Demasiadas solicitudes' in resp.get_data(as_text=True)

    # Comprobar que en estáticos no cuenta
    resp_static = rate_limit_app.get('/static/css/style.css')
    assert resp_static.status_code != 429


def test_route_specific_rate_limit(rate_limit_app, monkeypatch):
    # La ruta login tiene un límite de 10 req/min por IP. 
    # Usaremos una IP falsa y aumentaremos el límite global temporalmente para que no salte primero
    monkeypatch.setenv('RATE_LIMIT_GLOBAL', '100')
    
    # Mockear `google_oauth_enabled` si lo necesita la vista de login
    monkeypatch.setattr(auth, 'google_oauth_enabled', lambda: False)
    
    for _ in range(10):
        resp = rate_limit_app.get('/login')
        assert resp.status_code == 200
        
    # La número 11 en login debe fallar (por IP)
    resp = rate_limit_app.get('/login')
    assert resp.status_code == 429
    
    # Pero otra ruta debería seguir funcionando porque el global no se ha alcanzado
    resp = rate_limit_app.get('/')
    assert resp.status_code == 200


def test_admin_is_exempt(rate_limit_app, monkeypatch):
    monkeypatch.setenv('RATE_LIMIT_GLOBAL', '2')
    monkeypatch.setattr(rate_limit, '_is_admin', lambda: True)
    
    for _ in range(5):
        resp = rate_limit_app.get('/')
        assert resp.status_code == 200
        

def test_json_vs_html_response_types(rate_limit_app, monkeypatch):
    # Forzar el límite rápidamente
    monkeypatch.setenv('RATE_LIMIT_GLOBAL', '1')
    rate_limit_app.get('/') # 1 consumido
    
    # Petición normal -> devuelve HTML
    resp_html = rate_limit_app.get('/')
    assert resp_html.status_code == 429
    assert 'html' in resp_html.content_type.lower()
    
    # Petición JSON -> devuelve JSON
    resp_json = rate_limit_app.get('/', headers={'Accept': 'application/json'})
    assert resp_json.status_code == 429
    assert 'json' in resp_json.content_type.lower()
    assert 'error' in resp_json.get_json()


def test_window_sliding_behavior(rate_limit_app, monkeypatch):
    monkeypatch.setenv('RATE_LIMIT_GLOBAL', '2')
    
    now = time.time()
    
    # Controlamos el tiempo que ve la app
    time_mock = [now]
    monkeypatch.setattr(time, 'time', lambda: time_mock[0])
    
    # Hacemos 2 peticiones (el límite es 2)
    assert rate_limit_app.get('/').status_code == 200
    assert rate_limit_app.get('/').status_code == 200
    
    # La 3ra falla
    assert rate_limit_app.get('/').status_code == 429
    
    # Si pasa 1 ventana (60s), se limpia el historial
    time_mock[0] = now + 61
    assert rate_limit_app.get('/').status_code == 200
