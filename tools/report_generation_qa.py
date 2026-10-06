"""Índice local de las pruebas reales, sin dependencias ni servicios externos."""
from collections import Counter
import csv
import html
import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1] / 'qa_generaciones' / '2026-10-05'


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def build():
    matrix = load(ROOT / 'matriz.json', [])
    results = {case['id']: load(ROOT / case['id'] / 'resultado.json', {}) for case in matrix}
    completed = [result for result in results.values() if result]
    statuses = Counter(result['status'] for result in completed)
    config = load(ROOT / 'configuracion_publica.json', {})
    rows = []
    details = []
    esc = html.escape
    for case in matrix:
        cid = case['id']
        form = case['form']
        result = results[cid]
        bibliography_source = (result.get('bibliography') or {}).get('bibliography_source', '')
        status = result.get('status', 'pending')
        kind = 'Glosario' if form.get('document_kind') == 'glossary' else 'Informe'
        mode = 'Manual' if form.get('global-mode') == 'standard' or cid.startswith('L') else 'IA'
        options = ', '.join(name for key, name in [('incluir_introduccion', 'introducción'),
            ('incluir_conclusion', 'conclusión'), ('incluir_bibliografia', 'bibliografía')] if key in form) or 'sin secciones opcionales'
        subtitle = ' · '.join([form.get('instituto', 'universidad'), mode, options])
        if kind == 'Glosario':
            subtitle += f' · {case.get("expected_count")} términos · {case.get("upload") or form.get("glossary_source")}'
        issues = result.get('issues', []) + result.get('manual_observations', []) + ([result['error']] if result.get('error') else [])
        warnings = [message[1] for message in result.get('messages', [])]
        row = {'id': cid, 'estado': status, 'tipo': kind, 'modo': mode, 'institucion': form.get('instituto', 'universidad'),
               'titulo_solicitado': form['title'], 'titulo_final': result.get('title', ''), 'opciones': options,
               'terminos': case.get('expected_count', ''), 'archivo_de_entrada': case.get('upload', ''),
               'segundos': result.get('seconds', ''), 'tokens': result.get('tokens', ''), 'paginas': result.get('pages', ''),
               'origen_bibliografia': bibliography_source, 'llamadas_modelo': result.get('model_calls', ''),
               'incidencias': ' | '.join(issues), 'avisos': ' | '.join(warnings)}
        rows.append(row)
        directory = ROOT / cid
        links = ' '.join(f'<a href="{cid}/{filename}">{label}</a>' for filename, label in [
            ('documento.docx', 'Word'), ('documento.pdf', 'PDF'), ('solicitud.json', 'Petición completa'),
            ('formulario_enviado.json', 'Formulario final tras extracción'),
            ('respuestas.json', 'Peticiones y respuestas de IA/JEV'), ('inspeccion.json', 'Inspección'),
            ('texto_pdf.txt', 'Texto PDF'), ('extraccion.json', 'Extracción del archivo')]
            if (directory / filename).exists())
        gallery = ''.join(f'<a href="{cid}/{path.name}"><img loading="lazy" src="{cid}/{path.name}" alt="Páginas de {cid}"></a>'
                          for path in sorted(directory.glob('vista_*.png')))
        issue_html = ''.join(f'<p class="issue">{esc(issue)}</p>' for issue in issues)
        warning_html = ''.join(f'<p class="warning">{esc(warning)}</p>' for warning in warnings)
        option_table = ''.join(f'<tr><th>{esc(str(key))}</th><td>{esc(str(value))}</td></tr>' for key, value in form.items())
        details.append(f'''<details data-status="{status}" data-kind="{kind}" data-search="{esc(cid+' '+form['title']+' '+subtitle, quote=True)}">
<summary><b>{cid} · {esc(form['title'])}</b><span class="badge {status}">{status}</span>
<small>{esc(subtitle)}</small><small>{result.get('pages', '—')} páginas · {result.get('seconds', '—')} s · {result.get('tokens', '—')} tokens{' · Bibliografía: '+esc(bibliography_source) if bibliography_source else ''}</small></summary>
<div class="body"><nav>{links}</nav>{issue_html}{warning_html}<details><summary>Opciones enviadas</summary><table>{option_table}</table></details>
<div class="gallery">{gallery}</div></div></details>''')
    with (ROOT / 'resultados.csv').open('w', encoding='utf-8-sig', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    seconds = [result['seconds'] for result in completed]
    tokens = sum(result.get('tokens', 0) or 0 for result in completed)
    page_count = sum(result.get('pages', 0) or 0 for result in completed)
    note = f'{len(completed)}/{len(matrix)} casos terminados; {statuses["passed"]} correctos, {statuses["issues"]} con incidencias y {statuses["failed"]} fallidos.'
    body = f'''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EduLab · Pruebas de generación</title><style>
:root{{color-scheme:dark}}body{{font:16px system-ui;background:#0c0b1d;color:#eee;margin:0;padding:28px;max-width:1200px;margin:auto}}
h1{{margin-bottom:8px}}h1 span{{color:#13dbd2}}p,small{{color:#b7b7ca}}.metrics{{display:flex;gap:18px;flex-wrap:wrap;margin:20px 0}}
.metrics b{{background:#1c1932;border:1px solid #343045;padding:16px;border-radius:14px}}details{{border:1px solid #37334b;border-radius:12px;margin:12px 0;background:#181527}}
summary{{padding:16px;cursor:pointer}}summary small{{display:block;margin-top:7px}}.badge{{float:right;padding:3px 9px;border-radius:8px;background:#343046;font-size:13px}}
.passed{{color:#66e6bd}}.issues,.failed,.issue{{color:#ff8f99}}.warning{{color:#f1cf77}}.body{{padding:0 16px 16px}}nav{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
a{{color:#23d4db}}input,select{{font:inherit;color:inherit;background:#201d33;border:1px solid #49435e;border-radius:8px;padding:10px;margin:5px 8px 5px 0}}
table{{border-collapse:collapse;width:100%;table-layout:fixed;font-size:14px}}th,td{{padding:8px;text-align:left;border-bottom:1px solid #37334b;overflow-wrap:anywhere;white-space:pre-wrap}}th{{width:35%}}
.gallery{{display:flex;flex-wrap:wrap;gap:12px}}.gallery a{{width:min(100%,360px)}}img{{width:100%;height:auto;border-radius:8px}}[hidden]{{display:none!important}}
</style><h1>Edu<span>Lab</span> · Pruebas de generación</h1><p>{note}</p>
<p>Proveedor real: {esc(config.get('provider',''))} · Modelo: {esc(config.get('model',''))}. Intervalo mínimo: 35 segundos.</p>
<div class="metrics"><b>{page_count} páginas PDF</b><b>{tokens:,} tokens registrados</b><b>Mediana: {median(seconds) if seconds else 0:.1f} s</b></div>
<p>Cada muestra conserva su petición, las llamadas reales de IA/JEV y los archivos generados. Se usa una base de datos aislada, sin almacenar en R2 ni modificar cuentas reales.</p>
<p>Las comprobaciones de formato y contenido solicitado no certifican la exactitud científica de cada afirmación. Consulta las limitaciones y los errores corregidos en el informe.</p>
<nav><a href="INFORME.md">Informe de hallazgos</a><a href="resultados.csv">Tabla CSV</a><a href="matriz.json">Matriz completa</a><a href="antes_de_corregir/M09/documento.pdf">Ejemplo del fallo de bachillerato antes de corregir</a></nav>
<input id="search" type="search" placeholder="Buscar título o caso"><select id="status"><option value="">Todos los estados</option><option>passed</option><option>issues</option><option>failed</option><option>pending</option></select>
<select id="kind"><option value="">Todos los documentos</option><option>Informe</option><option>Glosario</option></select>
<section id="cases">{''.join(details)}</section>
<script>const search=document.querySelector('#search'),status=document.querySelector('#status'),kind=document.querySelector('#kind');
function filter(){{for(const item of document.querySelectorAll('#cases>details'))item.hidden=!(item.dataset.search.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())&&(!status.value||item.dataset.status===status.value)&&(!kind.value||item.dataset.kind===kind.value));}}
for(const control of [search,status,kind])control.addEventListener('input',filter);</script></html>'''
    (ROOT / 'index.html').write_text(body, encoding='utf-8')
    return note


if __name__ == '__main__':
    print(build())
