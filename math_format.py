"""Preserve delimited LaTeX and insert editable Office equations, without AI calls."""
from copy import deepcopy
from functools import lru_cache
import logging
import re

from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from latex2mathml.converter import convert as latex_to_mathml
from mathml2omml import convert as mathml_to_omml

logger = logging.getLogger(__name__)

# Match complete formulas only. A price or an unmatched dollar remains text.
_MATH_RE = re.compile(
    r'(?P<display>\\\[(?P<bracket>.*?)\\\]|(?<![\\$])\$\$(?P<double>.*?)\$\$)'
    r'|\\\((?P<inline>.*?)\\\)'
    r'|(?<![\\$])\$(?![\s$])(?P<single>[^$\r\n]+?)(?<![\s\\])\$(?![\d$])',
    re.S,
)


def math_spans(text):
    """Yield complete formulas with their delimiters and exact source offsets."""
    return _MATH_RE.finditer(text)


def has_math(text):
    return _MATH_RE.search(text) is not None


def protect_math(text):
    """Hide math from newline/Markdown cleanup; restore the original bytes later."""
    equations = {}
    prefix = '\ue000MATH'
    while prefix in text:
        prefix += 'X'

    def save(match):
        token = f'{prefix}{len(equations)}\ue001'
        equations[token] = match.group(0)
        return token

    return _MATH_RE.sub(save, text), equations


def restore_math(text, equations):
    for token, equation in equations.items():
        text = text.replace(token, equation)
    return text


@lru_cache(maxsize=256)
def _equation_xml(latex, display):
    # latex2mathml supports align*, the equivalent unnumbered form of aligned.
    latex = re.sub(r'\\(begin|end)\{aligned\}', r'\\\1{align*}', latex)
    mathml = latex_to_mathml(latex, display='block' if display else 'inline')
    omml = mathml_to_omml(mathml)
    equation = parse_xml(f'<root {nsdecls("m", "w")}>{omml}</root>')[0]
    # mathml2omml omits the degree slot for square roots. LibreOffice then
    # treats the radicand as the degree and displays empty placeholders.
    for radical in equation.iter(qn('m:rad')):
        if radical.find(qn('m:deg')) is None:
            properties = OxmlElement('m:radPr')
            hide_degree = OxmlElement('m:degHide')
            hide_degree.set(qn('m:val'), '1')
            properties.append(hide_degree)
            radical.insert(0, properties)
            radical.insert(1, OxmlElement('m:deg'))
    return equation


def append_math(paragraph, match):
    """Append one equation. Never discard the original if conversion fails."""
    latex = next(match.group(name) for name in ('bracket', 'double', 'inline', 'single')
                 if match.group(name) is not None)
    try:
        equation = deepcopy(_equation_xml(latex, match.group('display') is not None))
    except Exception:
        # Keep all operands and steps even for malformed/unsupported notation.
        # Avoid logging document content (which may contain personal information).
        logger.warning('No se pudo convertir una fórmula; se conserva su texto original.')
        paragraph.add_run(match.group(0))
        return False
    paragraph._p.append(equation)
    # Exact line heights clip tall fractions, roots, matrices and integrals.
    paragraph.paragraph_format.line_spacing = 1.4
    return True


def is_display_math(text):
    match = _MATH_RE.fullmatch(text.strip())
    return match is not None and match.group('display') is not None
