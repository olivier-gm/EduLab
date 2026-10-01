# 📝 Generador de Informes Académicos con IA (Gemini & Flask)

Un potente sistema web desarrollado en **Python / Flask** y potenciado con **Google Gemini (SDK Moderno `google-genai`)** diseñado para automatizar la creación de informes, trabajos escritos e investigaciones escolares o universitarias de calidad profesional en formatos **Microsoft Word (.docx)** y **PDF**.

El sistema no solo genera el contenido académico (Introducción, Desarrollo y Conclusión), sino que también formatea y maqueta automáticamente las páginas de presentación (portada) bajo estándares académicos, respetando la estructura organizativa de la institución.

---

## 🚀 Características Principales

- **Inteligencia Artificial de Vanguardia**: Integración nativa con la API de Google Gemini a través del SDK moderno (`google-genai`), utilizando el modelo de alto rendimiento `gemini-3.6-flash` para generar redacciones coherentes, profesionales y detalladas.
- **Validación Semántica de Títulos**: Filtro inteligente que analiza el título de la investigación antes de procesarla para validar su coherencia lógica y evitar contenidos inadecuados o no educativos.
- **Formateo Automatizado de Portadas**: Generación dinámica de la portada con datos institucionales (Universidad/Colegio, Carrera, Asignatura, Docente, Fecha y hasta 8 estudiantes con sus C.I./ID).
- **Procesamiento de Documentos (.docx)**: Uso de plantillas base de Microsoft Word (`python-docx`) con maquetación automática (fuente Arial 12pt, texto justificado, espaciado de línea Pt 21, saltos de página y subtítulos).
- **Conversión de Alta Fidelidad a PDF**: Integración directa con LibreOffice en modo *headless* para realizar conversiones directas de DOCX a PDF manteniendo el diseño original intacto.
- **Mis informes y limpieza automática**: cada usuario ve sus documentos generados en `/my_documents` con un contador en días y horas. El admin configura la conservación entre 1 y 8760 horas; el valor inicial es `FILE_RETENTION_HOURS` (24 por defecto). Cambiarlo solo afecta a documentos nuevos: los existentes y sus enlaces compartidos mantienen su fecha de vencimiento. La limpieza (`retention.py`) borra los archivos vencidos cada 15 minutos. Los archivos antiguos sin registro conservan el plazo del entorno.
- **Glosarios**: en el paso Contenido, elige Glosario y crea entre 1 y 100 términos por tema, o importa tu lista desde una imagen PNG/JPG/WebP, PDF, Word (.docx) o TXT UTF-8 (hasta 10 MB). Revisa los términos extraídos antes de generar. Incluyen portada, orden alfabético y definiciones de hasta 40 palabras, sin introducción ni conclusión. Los archivos subidos se procesan en memoria y no se guardan.
- **Bibliografía opcional**: apagada por defecto. En trabajos normales va en una página después de la conclusión (o al final si no hay conclusión); en glosarios se muestra una fuente junto a cada término. En modo manual se pegan las referencias. La bibliografía automática intenta primero Google Search, incluso con la búsqueda del desarrollo apagada. Si falla o no devuelve fuentes, la IA propone referencias de su conocimiento sin verificarlas en internet. El panel admin muestra el origen, fecha y motivo del respaldo de la última bibliografía completada. Los glosarios comparten una sola búsqueda entre sus tandas; si se agota la cuota, se reutiliza la pausa de 5 minutos antes de volver a intentar Google Search.

---

## 🛠️ Arquitectura del Proyecto

El panel admin incluye **Proveedor y modelo de IA**: elige Gemini directo u OpenRouter, escribe el modelo de cada uno y guarda sus claves API. Gemini directo usa IDs como `gemini-3.8-flash`; OpenRouter usa `google/gemini-3.8-flash`. Un campo de clave vacío conserva la actual; el checkbox permite quitar la clave del panel y volver a `GEMINI_API_KEY` u `OPENROUTER_API_KEY` del servidor. La configuración se aplica a las siguientes generaciones, también a introducción y conclusión en paralelo.

Las claves del panel se guardan cifradas en SQLite y no se incluyen en el HTML. Conserva **`instance/ai-secret.key` junto con la copia de seguridad de la base de datos**: es necesario para recuperar las claves al mover el servidor. `instance/` está excluido de Git. OpenRouter recibe texto, imágenes y PDF mediante su API; se solicita búsqueda nativa con `openrouter:web_search`, se leen las citas de su respuesta y se conserva el respaldo de bibliografía por IA. La búsqueda tiene un costo adicional; no usa la cuota de tu clave de Google AI Studio. El caché explícito de Google solo se utiliza en Gemini directo y se invalida al cambiar el modelo o la clave.

El título y cada término del glosario se evalúan con JEV: una consulta contiene una pregunta independiente por entrada. Las listas escritas o extraídas de archivos se validan antes de generar definiciones; en el modo por tema se valida primero el título y luego los términos generados antes de entregar el documento. Si JEV falla o no tiene clave, se usa una evaluación JSON con el proveedor de Gemini configurado. Los términos rechazados se muestran para corregirlos y no se eliminan silenciosamente. Las instrucciones de validación y generación aceptan vocabulario médico, anatómico y clínico en contexto educativo.

El sistema está dividido en módulos desacoplados y reutilizables:

- 📂 **`app.py`**: Controlador principal de Flask. Administra el enrutamiento, el ciclo de vida de las peticiones, la interacción con las vistas (HTML) y la entrega de las descargas en Word o PDF.
- 📂 **`IA.py`**: Núcleo de Inteligencia Artificial. Instancia el cliente GenAI, configura la generación y los filtros de seguridad, realiza el filtrado de títulos y consume el modelo de lenguaje de Google Gemini.
- 📂 **`form_processor.py`**: Procesador lógico de formularios. Limpia y valida la cantidad dinámica de integrantes (1 a 8), estandariza las mayúsculas/minúsculas y la sintaxis de fechas y prepara el diccionario de reemplazo para los marcadores de la plantilla.
- 📂 **`algorythms.py`**: Motor de formateo y exportación. Implementa el flujo de trabajo de reemplazo de etiquetas, maquetado de párrafos en Word y la invocación del comando LibreOffice para la creación de PDF.

---

## 📋 Requisitos del Sistema

### 1. Requisitos de Software
- **Python 3.10 o superior**
- **LibreOffice** (necesario para la conversión nativa a PDF). Por defecto, el sistema busca el ejecutable en: `C:/Program Files/LibreOffice/program/soffice.exe`

### 2. Dependencias de Python
Instaladas dentro de un entorno virtual, incluyendo:
- `Flask` (Framework web)
- `google-genai` (SDK Oficial moderno de Google Gemini)
- `python-docx` (Manipulación de plantillas Word)
- `lxml` (Procesamiento XML para docx)

---

## ⚙️ Instalación y Configuración

Sigue estos pasos para poner en marcha el proyecto de manera local en **Windows (PowerShell)**:

### 1. Clonar el repositorio
```powershell
git clone <url-del-repositorio>
cd essay
```

### 2. Crear y activar el entorno virtual (`.venv`)
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Instalar las dependencias
Asegúrate de instalar los requisitos de Python:
```powershell
pip install -r requirements.txt
pip install google-genai
```

### 4. Configurar la API Key de Gemini
Por motivos de seguridad y facilidad de desarrollo, el archivo **`IA.py`** utiliza una clave API para inicializar el cliente GenAI. Puedes sustituir la clave de pruebas en la línea 6 de `IA.py`:
```python
client = genai.Client(api_key='TU_API_KEY_DE_GEMINI')
```

---

## 🚀 Ejecución del Servidor de Desarrollo

Una vez completada la instalación, inicia el servidor local:

```powershell
python app.py
```

El servidor web estará disponible en [http://127.0.0.1:5000](http://127.0.0.1:5000).

---

## 🎓 Uso de la Aplicación

1. **Pantalla de Bienvenida**: Selecciona el tipo de informe que necesitas (`Universitario` o `Bachiller`).
2. **Formulario de Datos**: 
   - Completa el membrete de tu institución y datos académicos (Asignatura, Docente, Fecha, Ciudad).
   - Define el número de integrantes (máximo 8) e ingresa sus nombres y cédulas.
   - Escribe el título del tema principal y define hasta 8 subtítulos detallados para estructurar la investigación.
3. **Generación automática**: La IA validará el tema e iniciará la recopilación y estructuración del documento.
4. **Descargas**: Una vez completado, podrás descargar tu informe en formato **Word (.docx)** perfectamente editable o en **PDF** listo para imprimir.

---

## ⚠️ Notas y Solución de Problemas

> [!WARNING]
> **Error de Conversión a PDF (`FileNotFoundError: [WinError 2]`)**  
> Si al procesar el formulario recibes este error, se debe a que el sistema no encuentra **LibreOffice** instalado en la ruta de Windows por defecto. Para solucionarlo:
> 1. Asegúrate de tener instalado LibreOffice.
> 2. Si lo instalaste en una ruta diferente, edita la línea 13 de `algorythms.py` para reflejar la ruta correcta de `soffice.exe` en tu sistema:
>    ```python
>    LIBRE = 'C:/Ruta/A/Tu/Instalacion/LibreOffice/program/soffice.exe'
>    ```
