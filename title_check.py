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
import json
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
            'academic report about? ' + IA.EDUCATIONAL_CONTEXT
        ),
        'criteria': {
            'valid': (
                'A real subject, concept, person, event, work or question that can be '
                'researched, even if it is badly written, has typos, or is very short '
                'or informal. Animals, plants and common names are valid topics: '
                'EL PUMA, el gato, la rosa. Articles and uppercase text do not make '
                'a subject invalid. Medical terms and acronyms, including anatomy, sexual '
                'health, injuries and pathology, are valid academic subjects.'
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


def _call_jev(state, questions=None):
    """Una consulta a JEV. Lanza requests.RequestException o RuntimeError."""
    last = None
    for attempt in range(JEV_ATTEMPTS):
        try:
            response = requests.post(
                JEV_URL,
                headers={'Authorization': 'Bearer ' + _api_key()},
                json={'state': state, 'model': JEV_MODEL, 'questions': QUESTIONS if questions is None else questions},
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
            raise RuntimeError(f'JEV respondió {response.status_code}')
        return response.json()
    raise last


def _parse(payload, question_id=QUESTION_ID):
    answer = payload['answers'][question_id]
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


def check_glossary(title, terms=None, usage_sink=None, *, include_title=True):
    """Evalúa cada entrada por separado en una consulta; no elimina términos rechazados."""
    items = {'title': title} if include_title else {}
    items.update({f'term_{i}': term for i, term in enumerate(terms or [])})
    if not items:
        return
    state = {'topic': title, 'items': items}
    criteria = QUESTIONS[QUESTION_ID]['criteria']
    verdicts = None
    if jev_available():
        questions = {key: {
            'type': 'choice', 'criteria': criteria,
            'instructions': (
                f'Is only `items.{key}` a real academic subject or glossary concept? '
                'Use `topic` as context. Evaluate this item independently: other valid '
                'items must not make meaningless text valid. ' + IA.EDUCATIONAL_CONTEXT
            )} for key in items}
        try:
            payload = _call_jev(state, questions)
            verdicts = {key: _parse(payload, key) for key in items}
            logger.info('Glosario evaluado con JEV: %s entradas.', len(items))
        except Exception as exc:
            logger.warning('JEV no pudo evaluar el glosario (%s). Se usa Gemini como respaldo.',
                           type(exc).__name__)
    if verdicts is None:
        # Reutiliza la generación JSON y el proveedor configurado en el admin.
        from glossary import _json_generate
        result = _json_generate([json.dumps(state, ensure_ascii=False)], {
            'type': 'object', 'properties': {key: {'type': 'string', 'enum': list(criteria)}
                for key in items}, 'required': list(items), 'additionalProperties': False},
            'Evalúa cada campo de items de forma independiente como título o término de un '
            'glosario académico, usando topic como contexto. Clasifica cada uno con estas '
            'opciones: ' + json.dumps(criteria, ensure_ascii=False) + '. '
            'No declares válido un texto sin sentido porque otros términos sí sean válidos. '
            'No modifiques ni omitas entradas.', usage_sink)
        if not isinstance(result, dict) or set(result) != set(items) or any(
                not isinstance(value, str) or value not in criteria for value in result.values()):
            raise IA.GenerationError('invalid', 'No se pudo validar la lista completa del glosario. Inténtalo de nuevo.')
        verdicts = {key: TitleVerdict(value == 'valid', value, 'gemini') for key, value in result.items()}
    if 'title' in verdicts and not verdicts['title'].valid:
        why = _REASON_MESSAGES.get(verdicts['title'].reason, 'no parece un tema académico')
        raise ValueError(f'Revisa el título del glosario «{title}»: {why}.')
    invalid = [text for key, text in items.items() if key != 'title' and not verdicts[key].valid]
    if invalid:
        examples = ', '.join(f'«{term}»' for term in invalid[:5])
        extra = f' y {len(invalid) - 5} más' if len(invalid) > 5 else ''
        raise ValueError(f'Revisa estos términos del glosario: {examples}{extra}. '
                         'No se reconocen como conceptos académicos; corrige la lista antes de generar.')
