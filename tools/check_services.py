# -*- coding: utf-8 -*-
"""Comprueba con TUS credenciales reales que la base de datos y el almacenamiento
funcionan, antes de desplegar:

    python tools/check_services.py

Hace, y limpia después:
  Base de datos : se conecta, crea las tablas si faltan y lee los ajustes.
  Almacenamiento: sube un archivo de prueba, comprueba que existe, pide una URL
                  firmada, la descarga con HTTP y borra el archivo.
No muestra nunca claves ni contraseñas.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.getcwd(), '.env'))

import requests                                         # noqa: E402

import db                                               # noqa: E402
import storage                                          # noqa: E402

OK, FAIL = 'OK   ', 'FALLA'


def check_database():
    kind = 'PostgreSQL (DATABASE_URL)' if db.is_postgres() else f'SQLite ({db.DB_PATH})'
    print(f'Base de datos: {kind}')
    if not db.is_postgres():
        print('  AVISO: DATABASE_URL no está definida; se usaría SQLite local.')
    try:
        started = time.time()
        db.init_db()
        with db.standalone() as conn:
            users = conn.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
            tables = len(db._columns(conn, 'documents'))
        print(f'  {OK} conexión y tablas listas · {users} usuario(s) · {time.time() - started:.1f} s')
        return True
    except Exception as e:
        print(f'  {FAIL} {type(e).__name__}: {str(e)[:300]}')
        return False


def check_storage():
    kind = 'Cloudflare R2 / S3' if storage.is_remote() else f'carpeta local ({storage.OUTPUT_DIR}/)'
    print(f'Almacenamiento: {kind}')
    if not storage.is_remote():
        print('  AVISO: faltan variables R2_*; los archivos se guardarían en el servidor.')
        return True
    stem = f'_prueba_{int(time.time())}'
    os.makedirs(storage.OUTPUT_DIR, exist_ok=True)
    try:
        with open(storage.local_path(stem, 'docx'), 'wb') as f:
            f.write(b'prueba de almacenamiento')
        storage.publish(stem)
        print(f'  {OK} subida')
        assert storage.exists(stem, 'docx'), 'el archivo subido no aparece'
        print(f'  {OK} el archivo existe en el bucket')
        client = storage._get_client()
        url = client.generate_presigned_url(
            'get_object', Params={'Bucket': storage._bucket(), 'Key': storage._key(stem, 'docx')}, ExpiresIn=60)
        body = requests.get(url, timeout=15)
        assert body.status_code == 200 and body.content == b'prueba de almacenamiento', f'HTTP {body.status_code}'
        print(f'  {OK} descarga con URL firmada')
        return True
    except Exception as e:
        print(f'  {FAIL} {type(e).__name__}: {str(e)[:300]}')
        return False
    finally:
        try:
            storage.delete(stem)
            print(f'  {OK} archivo de prueba borrado')
        except Exception:
            pass


if __name__ == '__main__':
    results = [check_database(), check_storage()]
    sys.exit(0 if all(results) else 1)
