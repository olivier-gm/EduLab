from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/pdf'
OUT.mkdir(parents=True, exist_ok=True)
pdfmetrics.registerFont(TTFont('Arial', 'C:/Windows/Fonts/arial.ttf'))
pdfmetrics.registerFont(TTFont('ArialBold', 'C:/Windows/Fonts/arialbd.ttf'))
titles = [
'Diseño y validación de un sistema autónomo de trading algorítmico de criptoactivos mediante análisis de datos de mercado y modelos de decisión',
'Implementación y evaluación experimental de un sistema de trading automatizado de criptoactivos mediante modelos de decisión algorítmica',
'Desarrollo de una plataforma para el análisis, validación, optimización y ejecución automatizada de estrategias de trading algorítmico en mercados de criptoactivos']
contents = [
[
'Los mercados de criptoactivos generan grandes volúmenes de información cuya interpretación requiere tiempo, conocimientos técnicos y seguimiento constante. El análisis manual de indicadores, estructuras de mercado y condiciones de volatilidad dificulta mantener decisiones consistentes y oportunas. Asimismo, la integración de herramientas de análisis, toma de decisiones y ejecución representa un desafío tecnológico. Surge así la necesidad de investigar sistemas capaces de procesar información financiera y operar autónomamente mediante criterios algorítmicos verificables.',
'Se propone diseñar y validar un sistema autónomo que recopile datos de mercado, calcule indicadores técnicos y construya estructuras de información procesadas por modelos de decisión especializados. El sistema generará señales operativas y ejecutará operaciones automáticamente, incorporando controles de riesgo, supervisión y registro de decisiones. Su funcionamiento será evaluado mediante pruebas históricas y operaciones simuladas.',
'La investigación contribuirá al desarrollo de soluciones informáticas aplicadas al análisis financiero automatizado. Permitirá demostrar la integración de inteligencia artificial, algoritmos y procesamiento de datos, promoviendo competencias tecnológicas e investigación aplicada en el ámbito universitario.',
'Diseñar y validar un sistema autónomo de trading algorítmico de criptoactivos mediante el procesamiento de datos de mercado y modelos de decisión, para evaluar su funcionamiento y capacidad operativa.',
'Se aplicará desarrollo iterativo e incremental, acompañado de un enfoque cuantitativo y experimental. Se realizarán pruebas funcionales, simulaciones históricas y evaluaciones de autonomía, precisión operativa, latencia y reproducibilidad.',
'JavaScript, TypeScript, Python, Node.js, Express, React, PostgreSQL, Sequelize, NumPy, APIs de exchanges y modelos de decisión de IA, Git y Linux.'
],
[
'La toma de decisiones en mercados de criptoactivos exige interpretar simultáneamente múltiples indicadores y condiciones cambiantes de precio, volumen, liquidez y volatilidad. Los sistemas basados exclusivamente en reglas fijas pueden presentar limitaciones para integrar estas evidencias. Por otra parte, incorporar modelos de inteligencia artificial no demuestra por sí mismo una mejora en los resultados. Se requiere implementar y evaluar experimentalmente un sistema de decisión que permita determinar su comportamiento frente a estrategias convencionales, bajo condiciones reproducibles.',
'Se propone implementar un sistema de trading automatizado que utilice modelos de decisión algorítmica para interpretar estados estructurados del mercado. Mediante preguntas especializadas, los modelos estimarán probabilidades sobre determinadas condiciones del precio. Estas respuestas serán procesadas mediante reglas matemáticas para generar decisiones de compra, venta o espera, complementadas con filtros de tendencia, volatilidad y controles operativos.',
'El proyecto permitirá estudiar experimentalmente la aplicación de modelos de inteligencia artificial en procesos de decisión financiera automatizada. Su aporte científico y tecnológico consistirá en comparar algoritmos, medir resultados y desarrollar procedimientos reproducibles para evaluar sistemas inteligentes.',
'Implementar y evaluar experimentalmente un sistema de trading automatizado de criptoactivos mediante modelos de decisión algorítmica, para determinar su desempeño mediante pruebas históricas y comparaciones con estrategias algorítmicas convencionales.',
'Se utilizará desarrollo iterativo e incremental y evaluación experimental cuantitativa. Se efectuarán pruebas históricas controladas, comparaciones con algoritmos de reglas fijas y validación cronológica fuera de muestra, considerando rendimiento, riesgo y costos operativos.',
'JavaScript, TypeScript, Python, Node.js, Express, PostgreSQL, Sequelize, NumPy, APIs de modelos de decisión como JEV, APIs de Binance y BingX, Git y Linux.'
],
[
'El desarrollo de estrategias de trading algorítmico requiere analizar datos históricos, experimentar con distintos parámetros y comprobar su comportamiento antes de utilizarlas. Sin embargo, evaluar numerosas configuraciones puede demandar considerables recursos computacionales y dificultar la reproducción de resultados. Además, una estrategia con rendimientos históricos favorables no necesariamente conserva su efectividad ante nuevas condiciones del mercado. Esto plantea la necesidad de integrar herramientas de análisis, experimentación y optimización que faciliten la identificación y evaluación rigurosa de posibles ventajas estadísticas.',
'Se propone desarrollar una plataforma que integre análisis de mercado, modelos de decisión, backtesting especializado y ejecución automatizada de estrategias. Permitirá conservar registros detallados de las decisiones, modificar configuraciones algorítmicas y reproducir escenarios históricos alternativos para comparar resultados. Asimismo, incorporará herramientas de optimización y seguimiento para evaluar y aplicar estrategias bajo criterios definidos de rendimiento y riesgo.',
'La propuesta impulsará la investigación aplicada en análisis de datos e inteligencia artificial, proporcionando herramientas para experimentar con estrategias de inversión de manera sistemática y reproducible. Su impacto académico y social radicará en divulgar aplicaciones tecnológicas para el estudio de mercados financieros.',
'Desarrollar una plataforma para analizar, validar, optimizar y ejecutar automáticamente estrategias de trading algorítmico de criptoactivos, mediante modelos de decisión y experimentación con datos históricos.',
'Se empleará desarrollo iterativo e incremental, combinado con experimentación cuantitativa. Se aplicarán backtesting, simulaciones alternativas, comparación de configuraciones, validación fuera de muestra y pruebas de ejecución simulada, evaluando rendimiento, riesgo, estabilidad y costos.',
'JavaScript, TypeScript, Python, React, Vite, Node.js, Express, PostgreSQL, Sequelize, NumPy, APIs de modelos de IA y exchanges, Git, Nginx y Linux.'
]]
labels = ['Resumen de la problemática / Planteamiento breve', 'Resumen de la solución / Propuesta', 'Justificación e impacto', 'Objetivo general', 'Metodología tecnológica / Enfoque metodológico', 'Lenguajes de programación y tecnologías']
W,H=612,792
def para(c,text,x,y,width,size=11,leading=15,bold=False,align=TA_JUSTIFY):
    p=Paragraph(text,ParagraphStyle('p',fontName='ArialBold' if bold else 'Arial',fontSize=size,leading=leading,alignment=align,textColor='#191919'))
    _,height=p.wrap(width,800)
    p.drawOn(c,x,y-height)
    return y-height
for idx,(title,sections) in enumerate(zip(titles,contents),1):
    path=OUT/f'Propuesta_{idx}_Olivier_Garcia.pdf'
    c=canvas.Canvas(str(path),pagesize=(W,H))
    c.setTitle(title); c.setAuthor('OLIVIER GARCIA 31.114.123')
    c.drawImage(str(ROOT/'tmp/pdfs/logo.png'),232,670,width=148,height=66,preserveAspectRatio=True,mask='auto')
    y=650
    for line in ['República Bolivariana de Venezuela','Ministerio del Poder Popular para la Educación Universitaria, Ciencia y Tecnología','Universidad Nacional Experimental Rómulo Gallegos','Área de Ingeniería de Sistemas','Ingeniería Informática']:
        y=para(c,line,55,y,502,10,14,align=TA_CENTER)
    # El espacio del nombre del proyecto permanece vacío.
    y=para(c,title,65,455,482,15,21,True,TA_CENTER)
    y=para(c,'Propuesta de Proyecto de Grado I',65,y-20,482,12,16,align=TA_CENTER)
    y=para(c,'Línea de investigación propuesta: Análisis de datos',65,y-14,482,11,15,align=TA_CENTER)
    y=265
    for line,bold in [('Autor',False),('OLIVIER GARCIA',True),('Cédula: 31.114.123',False),('Sección: 2',False),('Tutor postulado: Erik Siso',False)]:
        y=para(c,line,65,y,482,11,17,bold,TA_CENTER)
    para(c,'San Juan de los Morros, octubre de 2026',65,70,482,10,14,True,TA_CENTER)
    c.showPage()
    y=para(c,f'PROPUESTA {idx}',54,750,504,12,16,True,TA_CENTER)
    y=para(c,title,54,y-12,504,12,16,True,TA_CENTER)
    y=para(c,'Línea de investigación propuesta: Análisis de datos',54,y-10,504,10,14,align=TA_CENTER)-16
    for label,body in zip(labels,sections):
        y=para(c,label,54,y,504,10.5,14,True,align=0)-4
        y=para(c,body,54,y,504,10.5,14)-12
    assert y>43,(idx,y)
    c.setFont('Arial',8); c.setFillColorRGB(.4,.4,.4)
    c.drawString(54,28,'Olivier Garcia | Ingeniería de Sistemas | Sección 2')
    c.drawRightString(558,28,'2')
    c.save()
    r=PdfReader(path)
    assert len(r.pages)==2
    text=' '.join(p.extract_text() for p in r.pages)
    assert 'Erik Siso' in text and '31.114.123' in text and 'LogMaster' not in text
    print(path, '2 páginas', 'contenido hasta y=',round(y))
