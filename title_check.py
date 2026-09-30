# title_check.py
"""Validación del título del trabajo con JEV (TypeSafe System One).

Antes se le preguntaba a Gemini "responde TRUE o FALSE" y se interpretaba el
texto libre. Con JEV es una pregunta de tipo Choice: devuelve la opción elegida
y una probabilidad por opción, así que el umbral es explícito, la respuesta no
depende de cómo redacte el modelo y además se sabe POR QUÉ se rechazó (texto sin
sentido, o un saludo/instrucción que no es un tema).

Si JEV no está disponible (sin clave, sin red, cuota, error del servicio) se usa
el validador de Gemini de IA.py como respaldo, para no bloquear a los usuarios.
Solo se lanza GenerationError si fallan las dos vías.

Documentación: https://docs.typesafe.ai/primitives/choice
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

import IA

logger = logging.getLogger(__name__)

JEV_URL = os.environ.get('JEV_URL', 'https://api.typesafe.ai/v1/systemone')
JEV_MODEL = os.environ.get('JEV_MODEL', 'jev-latest')
JEV_TIMEOUT = (3.05, 8)          # (conexión, lectura) en segundos
JEV_ATTEMPTS = 2                 # un reintento ante timeout / 5xx
# El título se acepta si la probabilidad de "valid" llega a este mínimo.
VALID_THRESHOLD = 0.5

QUESTION_ID = 'title_validity'
QUESTIONS = {
    QUESTION_ID: {
        'type': 'choice',
        'instructions': (
            'Is this text a real topic that a student could research and write an '
            'academic report about?'
        ),
        'criteria': {
            'valid': (
                'A real subject, concept, person, event, work or question that can be '
                'researched, even if it is badly written, has typos, or is very short '
                'or informal.'
            ),
            'gibberish': (
                'Random letters, keyboard mashing, repeated characters or text with no '
                'meaning in any language.'
            ),
            'not_a_topic': (
                'A greeting, insult, joke, chat message, personal question to an '
                'assistant, a bare first name, or an instruction that names no subject '
                'to research.'
            ),
        },
    }
}

_REASON_MESSAGES = {
    'gibberish': 'parece texto sin sentido o letras al azar',
    'not_a_topic': 'parece un saludo, una frase suelta o una instrucción, no un tema',
}


@dataclass(frozen=True)
class TitleVerdict:
    valid: bool
    reason: Optional[str] = None       # 'gibberish' | 'not_a_topic' | 'invalid' (respaldo)
    provider: str = 'jev'
    p_valid: Optional[float] = None

    def message(self, title):
        """Explicación para el usuario cuando el título se rechaza."""
        why = _REASON_MESSAGES.get(self.reason, 'no parece un tema que se pueda investigar')
        return (f'La IA no reconoce «{title}» como un tema para el trabajo: {why}. '
                'Escríbelo con palabras claras (por ejemplo, «Causas de la Revolución Francesa»).')


def _api_key():
    return (os.environ.get('JEV') or os.environ.get('TYPESAFE_API_KEY') or '').strip()


def jev_available():
    return bool(_api_key())


def _call_jev(state):
    """Una consulta a JEV. Lanza requests.RequestException o RuntimeError."""
    last = None
    for attempt in range(JEV_ATTEMPTS):
        try:
            response = requests.post(
                JEV_URL,
                headers={'Authorization': 'Bearer ' + _api_key()},
                json={'state': state, 'model': JEV_MODEL, 'questions': QUESTIONS},
                timeout=JEV_TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            continue
        if response.status_code >= 500:
            last = RuntimeError(f'JEV respondió {response.status_code}')
            continue
        if response.status_code != 200:
            # 4xx: clave inválida, cuota, petición mal formada… reintentar no ayuda.
            raise RuntimeError(f'JEV respondió {response.status_code}: {response.text[:200]}')
        return response.json()
    raise last


def _parse(payload):
    answer = payload['answers'][QUESTION_ID]
    probabilities = answer.get('probabilities') or {}
    p_valid = float(probabilities.get('valid', 1.0 if answer.get('choice') == 'valid' else 0.0))
    if p_valid >= VALID_THRESHOLD:
        return TitleVerdict(True, None, 'jev', p_valid)
    # Rechazado: la razón es la opción no válida más probable.
    others = {k: v for k, v in probabilities.items() if k != 'valid'}
    reason = max(others, key=others.get) if others else answer.get('choice')
    return TitleVerdict(False, reason, 'jev', p_valid)


def check_title(title, usage_sink=None):
    """Valida el título. Devuelve un TitleVerdict; lanza IA.GenerationError si
    ni JEV ni el respaldo de Gemini pudieron evaluarlo."""
    if jev_available():
        try:
            verdict = _parse(_call_jev(title))
            logger.info('Título evaluado con JEV: valid=%s p=%.2f reason=%s',
                        verdict.valid, verdict.p_valid, verdict.reason)
            return verdict
        except Exception as e:
            # Sin detalles de la petición (llevaría la clave); solo el motivo.
            logger.warning('JEV no pudo evaluar el título (%s: %s). Se usa Gemini como respaldo.',
                           type(e).__name__, str(e)[:160])
    else:
        logger.info('No hay clave JEV configurada; el título se evalúa con Gemini.')

    valid = IA.check_title(title, usage_sink=usage_sink)      # puede lanzar GenerationError
    return TitleVerdict(valid, None if valid else 'invalid', 'gemini')
