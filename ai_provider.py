"""Configuración de IA y adaptación de OpenRouter al flujo existente de Gemini."""
import base64
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

request_settings = ContextVar('ai_settings', default=None)


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


def configuration(default_model='gemini-3.5-flash-lite', values=None):
    import IA
    values = settings() if values is None else values
    provider = values['ai_provider']
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


def generate_content(*, model, config, contents):
    import IA
    provider, selected_model, key = configuration(model)
    if not key:
        raise IA.GenerationError('auth', 'Falta la clave API del proveedor seleccionado. Configúrala desde el panel admin.')
    if provider == 'gemini':
        return direct_client(key).models.generate_content(model=selected_model, config=config, contents=contents)
    return _openrouter(selected_model, key, config, contents)


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
