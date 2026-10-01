# storage.py
"""Dónde viven los informes generados (.docx y .pdf).

LibreOffice necesita archivos en disco, así que cada documento se arma en la
carpeta de trabajo `output/`. Después `publish()` decide el destino final:

  - Cloudflare R2 (o cualquier almacenamiento compatible con S3) cuando están
    definidas R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY y R2_BUCKET.
    Los archivos se suben y se borra la copia local, así el servidor no guarda
    nada: se puede reiniciar, redesplegar o mudar sin perder los informes.
  - La propia carpeta `output/` cuando no hay credenciales (desarrollo, pruebas).

El resto de la app solo usa estas funciones y no sabe en cuál de los dos está.
Las descargas con R2 no pasan por el servidor: se redirige a una URL firmada
que vence en pocos minutos, así que el bucket puede (y debe) ser privado.

Para mudarse a otro proveedor S3 (AWS, DigitalOcean Spaces, Backblaze, MinIO)
basta con cambiar S3_ENDPOINT_URL y las credenciales.
"""

import logging
import os
import urllib.parse

from flask import redirect, send_file

logger = logging.getLogger(__name__)

OUTPUT_DIR = 'output'
EXTENSIONS = ('docx', 'pdf')
CONTENT_TYPES = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pdf': 'application/pdf',
}
PRESIGN_SECONDS = 300

_client = None          # cliente boto3 (las pruebas inyectan uno falso)


class StorageError(Exception):
    """No se pudo guardar o leer un archivo del almacenamiento."""


# ── Configuración ─────────────────────────────────────────────────────

def _env(name):
    return os.environ.get(name, '').strip()


def is_remote():
    has_endpoint = bool(_env('S3_ENDPOINT_URL') or _env('R2_ACCOUNT_ID'))
    return bool(has_endpoint and _env('R2_ACCESS_KEY_ID') and _env('R2_SECRET_ACCESS_KEY')
                and _env('R2_BUCKET'))


def _bucket():
    return _env('R2_BUCKET')


def _key(stem, ext):
    prefix = _env('R2_PREFIX')
    prefix = (prefix.strip('/') + '/') if prefix else ''
    return f'{prefix}{stem}.{ext}'


def _get_client():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        endpoint = _env('S3_ENDPOINT_URL') or f"https://{_env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com"
        _client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=_env('R2_ACCESS_KEY_ID'),
            aws_secret_access_key=_env('R2_SECRET_ACCESS_KEY'),
            region_name='auto',                     # R2 no usa regiones
            config=Config(signature_version='s3v4', retries={'max_attempts': 3, 'mode': 'standard'},
                          connect_timeout=5, read_timeout=30, s3={'addressing_style': 'path'}),
        )
    return _client


def reset_client():
    global _client
    _client = None


def local_path(stem, ext):
    return os.path.join(OUTPUT_DIR, f'{stem}.{ext}')


def _is_missing(error):
    code = getattr(error, 'response', {}).get('Error', {}).get('Code', '')
    return str(code) in ('404', 'NoSuchKey', 'NotFound')


# ── Operaciones ───────────────────────────────────────────────────────

def exists(stem, ext):
    """¿Existe el archivo (en el almacenamiento definitivo)?"""
    if not is_remote():
        return os.path.isfile(local_path(stem, ext))
    try:
        _get_client().head_object(Bucket=_bucket(), Key=_key(stem, ext))
        return True
    except Exception as e:
        if not _is_missing(e):
            logger.error('No se pudo consultar %s en el almacenamiento: %s', _key(stem, ext), e)
        return False


def unique_stem(base):
    """`base` si está libre; si no, con un sufijo aleatorio (dos usuarios pueden
    escribir el mismo título y no deben pisarse el archivo)."""
    from algorythms import Document_process

    def taken(stem):
        return exists(stem, 'docx') or os.path.isfile(local_path(stem, 'docx'))

    stem = base
    while taken(stem):
        stem = f'{base}_{Document_process.generate_random_code()}'
    return stem


def publish(stem):
    """Pasa lo recién generado en `output/` al almacenamiento definitivo.

    Con R2 sube el .docx (obligatorio) y el .pdf (si se generó) y borra las
    copias locales. Lanza StorageError si no se pudo guardar el .docx."""
    if not os.path.isfile(local_path(stem, 'docx')):
        raise StorageError(f'No existe el documento generado: {stem}.docx')
    if not is_remote():
        return
    client = _get_client()
    try:
        for ext in EXTENSIONS:
            path = local_path(stem, ext)
            if os.path.isfile(path):
                client.upload_file(path, _bucket(), _key(stem, ext),
                                   ExtraArgs={'ContentType': CONTENT_TYPES[ext]})
    except Exception as e:
        logger.exception('No se pudo subir %s al almacenamiento', stem)
        raise StorageError(str(e)) from e
    for ext in EXTENSIONS:
        try:
            os.remove(local_path(stem, ext))
        except OSError:
            pass


def serve(stem, ext, download_name=None):
    """Respuesta de Flask que entrega el archivo como descarga.
    Lanza FileNotFoundError si no existe."""
    if not exists(stem, ext):
        raise FileNotFoundError(f'{stem}.{ext}')
    name = download_name or f'{stem}.{ext}'
    if not is_remote():
        return send_file(os.path.abspath(local_path(stem, ext)), as_attachment=True, download_name=name)
    ascii_name = name.encode('ascii', 'ignore').decode().replace('"', '') or f'documento.{ext}'
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}"
    url = _get_client().generate_presigned_url(
        'get_object',
        Params={'Bucket': _bucket(), 'Key': _key(stem, ext),
                'ResponseContentDisposition': disposition, 'ResponseContentType': CONTENT_TYPES[ext]},
        ExpiresIn=PRESIGN_SECONDS,
    )
    return redirect(url, code=302)


def delete(stem):
    """Borra el .docx y el .pdf (si existen) de ambos lugares."""
    for ext in EXTENSIONS:
        try:
            os.remove(local_path(stem, ext))
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning('No se pudo borrar %s: %s', local_path(stem, ext), e)
        if is_remote():
            try:
                _get_client().delete_object(Bucket=_bucket(), Key=_key(stem, ext))
            except Exception as e:
                logger.warning('No se pudo borrar %s del almacenamiento: %s', _key(stem, ext), e)


def list_files():
    """[{'stem', 'ext', 'modified'}] de lo guardado (modified = epoch en segundos)."""
    found = []
    if is_remote():
        prefix = _key('', '')[:-1]            # 'reports/.' -> 'reports/'
        paginator = _get_client().get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
            for item in page.get('Contents', []):
                name = item['Key'][len(prefix):]
                stem, _, ext = name.rpartition('.')
                if stem and ext in EXTENSIONS:
                    found.append({'stem': stem, 'ext': ext, 'modified': item['LastModified'].timestamp()})
        return found
    if os.path.isdir(OUTPUT_DIR):
        for name in os.listdir(OUTPUT_DIR):
            stem, _, ext = name.rpartition('.')
            if stem and ext.lower() in EXTENSIONS:
                try:
                    found.append({'stem': stem, 'ext': ext.lower(),
                                  'modified': os.path.getmtime(os.path.join(OUTPUT_DIR, name))})
                except OSError:
                    pass
    return found


def delete_file(stem, ext):
    """Borra un único archivo (lo usa la limpieza de huérfanos)."""
    if is_remote():
        _get_client().delete_object(Bucket=_bucket(), Key=_key(stem, ext))
        return
    os.remove(local_path(stem, ext))


def stems_available():
    """{stem: {'docx', 'pdf'}} de lo que existe ahora: una sola consulta al
    almacenamiento para pintar toda la lista de "Mis informes"."""
    result = {}
    for item in list_files():
        result.setdefault(item['stem'], set()).add(item['ext'])
    return result


def local_scratch_cleanup(max_age_seconds):
    """Con R2, `output/` es solo carpeta de trabajo: borra restos viejos de
    generaciones que fallaron a medias. No hace nada en modo local."""
    if not is_remote() or not os.path.isdir(OUTPUT_DIR):
        return 0
    import time
    removed = 0
    for name in os.listdir(OUTPUT_DIR):
        if name.rpartition('.')[2].lower() not in EXTENSIONS:
            continue
        path = os.path.join(OUTPUT_DIR, name)
        try:
            if time.time() - os.path.getmtime(path) > max_age_seconds:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed
