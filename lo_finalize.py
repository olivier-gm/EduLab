# -*- coding: utf-8 -*-
"""Finaliza un documento usando LibreOffice a través de UNO.

Este script NO se ejecuta con el intérprete del proyecto: se lanza con el
Python que trae LibreOffice (el único que tiene el módulo `uno`), desde
`Document_process.convert()`.

Hace dos cosas que `soffice --convert-to pdf` no puede hacer:

  1. Actualiza la tabla de contenido (índice) para que quede con sus
     entradas y números de página reales. LibreOffice no actualiza los
     índices al cargar, ni con <w:updateFields> ni con w:dirty, así que hay
     que pedírselo explícitamente con XDocumentIndex.update().
  2. Guarda el .docx ya con el índice poblado y exporta el .pdf desde ese
     mismo documento en memoria, de modo que ambos formatos salgan con el
     índice hecho y el usuario no tenga que pulsar nada en Word.

LibreOffice se lanza aquí mismo (no con officehelper.bootstrap) por tres
razones:
  - officehelper no pasa --headless/--invisible/--norestore: mostraba
    ventanas en pantalla y un cuadro de "recuperar documentos" pendiente
    podía bloquear la generación hasta el timeout.
  - Se le da la ruta como argumento de lista, sin shell: no hace falta
    entrecomillar "C:\\Program Files\\...".
  - Se usa un perfil propio y aislado (ver Document_process._lo_profile_arg):
    no comparte instancia con el LibreOffice del usuario, así que terminar
    este proceso nunca cierra las ventanas que el usuario tenga abiertas.

Uso:
    <lo_python> lo_finalize.py <ruta.docx> <ruta_salida.pdf> <ruta_soffice> [-env:UserInstallation=...]
"""
import os
import subprocess
import sys
import time

import uno            # noqa: F401  (necesario para inicializar el puente UNO)
import unohelper
from com.sun.star.beans import PropertyValue

CONNECT_TIMEOUT = 90   # segundos esperando a que LibreOffice acepte conexiones


def _pv(name, value):
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def _start_office(soffice, profile_arg):
    """Lanza soffice sin interfaz y devuelve (proceso, contexto UNO)."""
    pipe = 'gullieth_%d' % os.getpid()
    command = [
        soffice, '--headless', '--invisible', '--norestore', '--nologo',
        '--nodefault', '--nofirststartwizard',
        '--accept=pipe,name=%s;urp;' % pipe,
    ]
    if profile_arg:
        command.insert(1, profile_arg)
    process = subprocess.Popen(command)

    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        'com.sun.star.bridge.UnoUrlResolver', local)
    url = 'uno:pipe,name=%s;urp;StarOffice.ComponentContext' % pipe

    deadline = time.time() + CONNECT_TIMEOUT
    delay = 0.3
    while True:
        try:
            return process, resolver.resolve(url)
        except Exception:
            if process.poll() is not None:
                raise RuntimeError('LibreOffice se cerró al iniciar (código %s)' % process.returncode)
            if time.time() > deadline:
                _kill(process)
                raise RuntimeError('LibreOffice no aceptó conexiones en %ds' % CONNECT_TIMEOUT)
            time.sleep(delay)
            delay = min(delay * 1.5, 2)


def _kill(process):
    """Cierra el proceso y sus hijos (en Windows soffice.exe lanza soffice.bin)."""
    if process.poll() is not None:
        return
    if sys.platform.startswith('win'):
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        process.kill()


def main():
    if len(sys.argv) < 4:
        print('uso: lo_finalize.py <docx> <pdf> <soffice> [-env:UserInstallation=...]', file=sys.stderr)
        return 2

    docx_path = os.path.abspath(sys.argv[1])
    pdf_path = os.path.abspath(sys.argv[2])
    soffice = sys.argv[3]
    profile_arg = sys.argv[4] if len(sys.argv) > 4 else None

    if not os.path.isfile(docx_path):
        print('no existe el docx: %s' % docx_path, file=sys.stderr)
        return 2

    process, ctx = _start_office(soffice, profile_arg)
    desktop = ctx.ServiceManager.createInstanceWithContext(
        'com.sun.star.frame.Desktop', ctx)

    doc = None
    try:
        doc = desktop.loadComponentFromURL(
            unohelper.systemPathToFileUrl(docx_path), '_blank', 0,
            (_pv('Hidden', True), _pv('ReadOnly', False)),
        )
        if doc is None:
            print('LibreOffice no pudo abrir el documento', file=sys.stderr)
            return 1

        # 1) Refrescar campos y poblar el índice
        try:
            doc.getTextFields().refresh()
        except Exception as e:
            print('aviso: no se pudieron refrescar los campos: %s' % e, file=sys.stderr)

        indexes = doc.getDocumentIndexes()
        for i in range(indexes.getCount()):
            indexes.getByIndex(i).update()
        print('indices actualizados: %d' % indexes.getCount())

        # 2) Guardar el docx ya con el índice poblado
        doc.storeToURL(
            unohelper.systemPathToFileUrl(docx_path),
            (_pv('FilterName', 'MS Word 2007 XML'), _pv('Overwrite', True)),
        )

        # 3) Exportar el PDF desde el mismo documento
        doc.storeToURL(
            unohelper.systemPathToFileUrl(pdf_path),
            (_pv('FilterName', 'writer_pdf_Export'), _pv('Overwrite', True)),
        )
        print('pdf generado: %s' % pdf_path)
        return 0
    finally:
        if doc is not None:
            try:
                doc.close(False)
            except Exception:
                pass
        try:
            desktop.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=10)
        except Exception:
            _kill(process)


if __name__ == '__main__':
    sys.exit(main())
