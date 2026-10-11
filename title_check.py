# title_check.py
"""Validación académica con JEV o Microsoft Decision, seleccionable en admin.

Antes se le preguntaba a Gemini "responde TRUE o FALSE" y se interpretaba el
texto libre. Con JEV es una pregunta de tipo Choice: devuelve la opción elegida
y una probabilidad por opción, así que el umbral es explícito, la respuesta no
depende de cómo redacte el modelo y además se sabe POR QUÉ se rechazó (texto sin
sentido, o un saludo/instrucción que no es un tema).

Si el validador no está disponible (sin clave, red, cuota, error del servicio) se usa
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
import ai_provider

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


class GlossaryTermsError(ValueError):
    """Incluye los términos rechazados para reparar solo listas elegidas por la IA."""
    def __init__(self, message, terms):
        super().__init__(message)
        self.terms = terms


def _api_key():
    if decision_provider() == 'openrouter':
        return ai_provider.configuration(provider='openrouter')[2].strip()
    return (os.environ.get('JEV') or os.environ.get('TYPESAFE_API_KEY') or '').strip()


def decision_provider():
    return ai_provider.settings().get('decision_provider', 'jev')


def jev_available():
    """Disponibilidad del validador seleccionado; nombre conservado para sus callers."""
    try:
        return bool(_api_key())
    except IA.GenerationError:
        return False


def _call_jev(state, questions=None):
    """Consulta al validador seleccionado con el contrato compartido de decisiones."""
    provider = decision_provider()
    url = 'https://openrouter.ai/api/alpha/decisions' if provider == 'openrouter' else JEV_URL
    model = 'microsoft/microsoft-decision-1' if provider == 'openrouter' else JEV_MODEL
    questions = QUESTIONS if questions is None else questions
    if provider == 'openrouter':
        questions = {key: {**question, 'criteria': {
            option: json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
            for option, value in question['criteria'].items()}}
            for key, question in questions.items()}
    last = None
    for attempt in range(JEV_ATTEMPTS):
        try:
            response = requests.post(
                url,
                headers={'Authorization': 'Bearer ' + _api_key()},
                json={'state': state, 'model': model, 'questions': questions},
                timeout=JEV_TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            continue
        if response.status_code >= 500:
            last = RuntimeError(f'{model} respondió {response.status_code}')
            continue
        if response.status_code != 200:
            # 4xx: clave inválida, cuota, petición mal formada… reintentar no ayuda.
            raise RuntimeError(f'{model} respondió {response.status_code}')
        payload = response.json()
        if provider == 'openrouter':
            ai_provider._trace_attempt('openrouter', payload.get('model', model), True)
        return payload
    raise last


def _parse(payload, question_id=QUESTION_ID):
    answer = payload['answers'][question_id]
    probabilities = answer.get('probabilities') or {}
    p_valid = float(probabilities.get('valid', 1.0 if answer.get('choice') == 'valid' else 0.0))
    if p_valid >= VALID_THRESHOLD:
        return TitleVerdict(True, None, decision_provider(), p_valid)
    # Rechazado: la razón es la opción no válida más probable.
    others = {k: v for k, v in probabilities.items() if k != 'valid'}
    reason = max(others, key=others.get) if others else answer.get('choice')
    return TitleVerdict(False, reason, decision_provider(), p_valid)


def check_title(title, usage_sink=None):
    """Valida el título. Devuelve un TitleVerdict; lanza IA.GenerationError si
    ni JEV ni el respaldo de Gemini pudieron evaluarlo."""
    if jev_available():
        try:
            verdict = _parse(_call_jev(title))
            logger.info('Título evaluado con %s: valid=%s p=%.2f reason=%s',
                        decision_provider(), verdict.valid, verdict.p_valid, verdict.reason)
            return verdict
        except Exception as e:
            # Sin detalles de la petición (llevaría la clave); solo el motivo.
            logger.warning('%s no pudo evaluar el título (%s: %s). Se usa Gemini como respaldo.',
                           decision_provider(), type(e).__name__, str(e)[:160])
    else:
        logger.info('No hay clave del validador %s; el título se evalúa con Gemini.', decision_provider())

    valid = IA.check_title(title, usage_sink=usage_sink)      # puede lanzar GenerationError
    return TitleVerdict(valid, None if valid else 'invalid', 'gemini')


def _glossary_verdicts(title, items, usage_sink):
    """Valida una tanda acotada con JEV o con el respaldo del proveedor activo."""
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
            logger.info('Glosario evaluado con %s: %s entradas.', decision_provider(), len(items))
        except Exception as exc:
            logger.warning('%s no pudo evaluar el glosario (%s). Se usa Gemini como respaldo.',
                           decision_provider(), type(exc).__name__)
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
    return verdicts


def check_glossary(title, terms=None, usage_sink=None, *, include_title=True):
    """Evalúa cada entrada; las tandas evitan esquemas demasiado grandes para Gemini."""
    items = {'title': title} if include_title else {}
    items.update({f'term_{i}': term for i, term in enumerate(terms or [])})
    if not items:
        return
    pairs = list(items.items())
    verdicts = {}
    for offset in range(0, len(pairs), 50):
        verdicts.update(_glossary_verdicts(title, dict(pairs[offset:offset + 50]), usage_sink))
    if 'title' in verdicts and not verdicts['title'].valid:
        why = _REASON_MESSAGES.get(verdicts['title'].reason, 'no parece un tema académico')
        raise ValueError(f'Revisa el título del glosario «{title}»: {why}.')
    invalid = [text for key, text in items.items() if key != 'title' and not verdicts[key].valid]
    if invalid:
        examples = ', '.join(f'«{term}»' for term in invalid[:5])
        extra = f' y {len(invalid) - 5} más' if len(invalid) > 5 else ''
        raise GlossaryTermsError(f'Revisa estos términos del glosario: {examples}{extra}. '
                                 'No se reconocen como conceptos académicos; corrige la lista antes de generar.', invalid)
