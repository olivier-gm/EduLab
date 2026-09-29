# -*- coding: utf-8 -*-
"""Guardia contra archivos corruptos: un carácter de control (p. ej. un byte
nulo dentro de una regex) dejaba app.py sin poder importarse y tumbaba el
servidor con 'source code cannot contain null bytes'."""
import glob
import py_compile

import pytest

ROOT_FILES = (
    glob.glob('*.py') + glob.glob('tests/*.py') + glob.glob('static/js/*.js')
    + glob.glob('static/css/*.css') + glob.glob('templates/*.html')
)


@pytest.mark.parametrize('path', sorted(ROOT_FILES))
def test_sin_caracteres_de_control(path):
    data = open(path, 'rb').read()
    bad = [i for i, b in enumerate(data) if b < 32 and b not in (9, 10, 13)]
    assert not bad, f'{path} tiene caracteres de control en los bytes {bad[:5]}'


@pytest.mark.parametrize('path', sorted(glob.glob('*.py')))
def test_los_modulos_compilan(path):
    py_compile.compile(path, doraise=True)
