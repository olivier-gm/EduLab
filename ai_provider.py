"""Configuración de IA y adaptación de OpenRouter al flujo existente de Gemini."""
import base64
import copy
import logging
import os
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace as NS

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, has_app_context
from google import genai

import db

logger = logging.getLogger(__name__)

request_settings = ContextVar('ai_settings', default=None)

# Errores (según IA.classify_error) que justifican probar con el otro proveedor.
# Los de autenticación, petición inválida o contenido bloqueado no: repetirlos en
# otro motor no los arregla (o, en el caso de un bloqueo, es repetir el contenido).
FALLBACK_CODES = ('quota', 'unavailable', 'unknown')


def settings():
    snapshot = request_settings.get()
    return snapshot if snapshot is not None else (db.get_settings() if has_app_context() else db.SETTING_DEFAULTS)


def _cipher():
    directory = Path(current_app.instance_path)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'ai-secret.key'
    if not path.exists():
        try:
            with path.open('xb') as stream:
                stream.write(Fernet.generate_key())
        except FileExistsError:
            pass
    return Fernet(path.read_bytes())


def encrypt_key(value):
    return _cipher().encrypt(value.encode()).decode()


def configuration(default_model='gemini-3.5-flash-lite', values=None, provider=None):
    """(proveedor, modelo, clave) del proveedor activo o, con `provider`, del indicado."""
    import IA
    values = settings() if values is None else values
    provider = provider or values['ai_provider']
    prefix = 'openrouter' if provider == 'openrouter' else 'gemini'
    model = values[f'{prefix}_model'] or default_model
    encrypted = values[f'{prefix}_api_key']
    if encrypted:
        try:
            key = _cipher().decrypt(encrypted.encode()).decode()
        except (InvalidToken, OSError, ValueError):
            raise IA.GenerationError('auth', 'No se pudo leer la clave guardada. Vuelve a guardarla desde el panel admin.') from None
    else:
        key = os.environ.get('OPENROUTER_API_KEY' if prefix == 'openrouter' else 'GEMINI_API_KEY', '')
    return provider, model, key


@lru_cache(maxsize=4)
def google_client(key):
    return genai.Client(api_key=key)


def direct_client(key):
    import IA
    # Reutiliza el cliente original y conserva los reemplazos de las pruebas.
    if key == os.environ.get('GEMINI_API_KEY', '') and IA.client is not None:
        return IA.client
    return google_client(key)


def search_enabled(provider=None, values=None):
    """¿La búsqueda web (Google Search / OpenRouter web search) está activada para ese
    proveedor? Sin `provider`, el activo. Se configura en el panel admin."""
    values = settings() if values is None else values
    provider = provider or values['ai_provider']
    return values.get(f'{provider}_search_enabled', '1') == '1'


def _other_provider(values):
    return 'openrouter' if values['ai_provider'] == 'gemini' else 'gemini'


def _fallback_configuration(default_model='gemini-3.5-flash-lite', values=None):
    """(proveedor, modelo, clave) del OTRO proveedor, o None si el fallback está
    apagado o ese proveedor no tiene clave."""
    values = settings() if values is None else values
    if values.get('fallback_enabled') != '1':
        return None
    try:
        provider, model, key = configuration(default_model, values, provider=_other_provider(values))
    except Exception:           # clave guardada ilegible: sin fallback, no se rompe el flujo principal
        return None
    return (provider, model, key) if key else None


def _without_tools(config):
    """Copia de la configuración sin herramientas (sin búsqueda web)."""
    if not getattr(config, 'tools', None):
        return config
    try:
        return config.model_copy(update={'tools': None})
    except AttributeError:
        clone = copy.copy(config)
        clone.tools = None
        return clone


def _call(provider, model, key, config, contents, values):
    if getattr(config, 'tools', None) and not search_enabled(provider, values):
        logger.info('La búsqueda web está desactivada para %s: se genera sin búsqueda.', provider)
        config = _without_tools(config)
    if provider == 'gemini':
        return direct_client(key).models.generate_content(model=model, config=config, contents=contents)
    return _openrouter(model, key, config, contents)


def generate_content(*, model, config, contents, fallback_config=None, fallback_contents=None):
    """Genera con el proveedor activo; si falla y el fallback está activado, con el otro.

    fallback_config / fallback_contents: versión de la petición para el otro
    proveedor cuando la principal depende de algo propio del primero (por ejemplo,
    el caché de contexto de Gemini, que no existe en OpenRouter y dejaría sin
    instrucciones ni ejemplos a la petición). Si no se pasan, se reusa la misma.
    """
    import IA
    values = settings()
    provider, selected_model, key = configuration(model, values)
    if not key:
        raise IA.GenerationError('auth', 'Falta la clave API del proveedor seleccionado. Configúrala desde el panel admin.')
    try:
        return _call(provider, selected_model, key, config, contents, values)
    except Exception as primary_error:
        fallback = _fallback_configuration(model, values)
        if fallback is None:
            raise
        error = IA.classify_error(primary_error)
        if error.code not in FALLBACK_CODES:
            logger.info('%s falló (%s): ese tipo de error no se reintenta con el otro proveedor.',
                        provider, error.code)
            raise
        fb_provider, fb_model, fb_key = fallback
        logger.warning('Proveedor de IA %s falló (%s): %s. Fallback automático a %s.',
                       provider, error.code, str(primary_error)[:200], fb_provider)
        try:
            response = _call(fb_provider, fb_model, fb_key, fallback_config or config,
                             fallback_contents or contents, values)
        except Exception as fallback_error:
            logger.error('El fallback a %s también falló: %s', fb_provider, str(fallback_error)[:200])
            raise primary_error
        logger.warning('El fallback a %s respondió correctamente (el principal era %s).', fb_provider, provider)
        return response


def _parts(parts):
    import IA
    result = []
    for part in parts:
        if isinstance(part, str):
            result.append({'type': 'text', 'text': part})
        elif getattr(part, 'text', None) is not None:
            result.append({'type': 'text', 'text': part.text})
        elif getattr(part, 'inline_data', None):
            blob = part.inline_data
            uri = f'data:{blob.mime_type};base64,{base64.b64encode(blob.data).decode()}'
            if blob.mime_type == 'application/pdf':
                result.append({'type': 'file', 'file': {'filename': 'terms.pdf', 'file_data': uri}})
            elif blob.mime_type.startswith('image/'):
                result.append({'type': 'image_url', 'image_url': {'url': uri}})
            else:
                raise IA.GenerationError('bad_request', 'El formato del archivo no se puede enviar a OpenRouter.')
        else:
            raise IA.GenerationError('bad_request', 'El contenido no se puede enviar a OpenRouter.')
    return result


def _openrouter(model, key, config, contents):
    import IA
    messages = []
    if config.system_instruction:
        instruction = config.system_instruction
        text = instruction if isinstance(instruction, str) else '\n'.join(p.text or '' for p in instruction.parts)
        messages.append({'role': 'system', 'content': text})
    for item in contents if isinstance(contents, list) else [contents]:
        role = 'assistant' if getattr(item, 'role', None) == 'model' else 'user'
        parts = _parts(item.parts if getattr(item, 'parts', None) is not None else [item])
        if messages and messages[-1]['role'] == role:
            messages[-1]['content'].extend(parts)
        else:
            messages.append({'role': role, 'content': parts})
    body = {'model': model, 'messages': messages, 'provider': {'require_parameters': True}}
    for name, value in [('temperature', config.temperature), ('top_p', config.top_p),
                        ('max_tokens', config.max_output_tokens)]:
        if value is not None:
            body[name] = value
    if config.response_json_schema:
        body['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'document', 'strict': True, 'schema': config.response_json_schema}}
    if config.tools:
        body['tools'] = [{'type': 'openrouter:web_search', 'parameters': {'engine': 'native'}}]
        body['max_tool_calls'] = 3
    if any(p['type'] == 'file' for msg in messages if isinstance(msg['content'], list) for p in msg['content']):
        body['plugins'] = [{'id': 'file-parser', 'pdf': {'engine': 'native'}}]
    try:
        response = requests.post('https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json=body, timeout=(10, 120))
        data = response.json()
    except requests.RequestException:
        raise IA.GenerationError('unavailable', 'OpenRouter no respondió. Inténtalo de nuevo.') from None
    except ValueError:
        raise IA.GenerationError('unavailable', 'OpenRouter devolvió una respuesta ilegible.') from None
    if not isinstance(data, dict):
        raise IA.GenerationError('unavailable', 'OpenRouter devolvió una respuesta ilegible.')
    if response.status_code >= 400 or data.get('error'):
        code = response.status_code if response.status_code >= 400 else data['error'].get('code', 502)
        error = requests.HTTPError(f'OpenRouter HTTP {code}')
        error.code = code
        raise error
    choices = data.get('choices') or []
    if not choices:
        raise IA.GenerationError('empty', 'OpenRouter no devolvió contenido.')
    choice = choices[0]
    message = choice.get('message') or {}
    text = message.get('content') or ''
    if isinstance(text, list):
        text = '\n'.join(p.get('text', '') for p in text if p.get('type') == 'text')
    chunks = []
    for annotation in message.get('annotations') or []:
        if annotation.get('type') == 'url_citation':
            citation = annotation.get('url_citation') or {}
            chunks.append(NS(web=NS(uri=citation.get('url', ''), title=citation.get('title', ''))))
    usage = data.get('usage') or {}
    total = usage.get('total_tokens')
    if total is None:
        total = (usage.get('prompt_tokens', usage.get('input_tokens', 0)) or 0) + (usage.get('completion_tokens', usage.get('output_tokens', 0)) or 0)
    finish = {'length': 'MAX_TOKENS', 'content_filter': 'SAFETY'}.get(choice.get('finish_reason'), 'STOP')
    return NS(text=text, candidates=[NS(finish_reason=finish,
        grounding_metadata=NS(grounding_chunks=chunks))], usage_metadata=NS(total_token_count=total))
