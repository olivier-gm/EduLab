# 📝 EduLab · Informes y glosarios académicos con IA

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
- **Glosarios**: en el paso Contenido, elige Glosario. El máximo de términos depende de tu plan y se configura desde admin (100 para Premium y 300 para Pro por defecto; Recarga no incluye glosarios). Importa tu lista desde una imagen PNG/JPG/WebP, PDF, Word (.docx) o TXT UTF-8 (hasta 10 MB). Revisa los términos extraídos antes de generar. Incluyen portada, orden alfabético y definiciones breves, sin introducción ni conclusión. Los archivos subidos se procesan en memoria y no se guardan.
- **Bibliografía opcional**: apagada por defecto. En trabajos normales va en una página después de la conclusión (o al final si no hay conclusión); en glosarios se muestra una fuente junto a cada término. En modo manual se pegan las referencias. La bibliografía automática intenta primero Google Search, incluso con la búsqueda del desarrollo apagada. Si falla o no devuelve fuentes, la IA propone referencias de su conocimiento sin verificarlas en internet. El panel admin muestra el origen, fecha y motivo del respaldo de la última bibliografía completada. Los glosarios comparten una sola búsqueda entre sus tandas; si se agota la cuota, se reutiliza la pausa de 5 minutos antes de volver a intentar Google Search.

---

## 🛠️ Arquitectura del Proyecto

El registro normal exige verificar un código de seis dígitos antes de crear el usuario. Configura `GMAIL_USER` (correo emisor) y `GMAIL_APP_PASSWORD` (contraseña de aplicación de Gmail) en las variables del servidor de producción o en `.env` local. Gmail debe tener verificación en dos pasos; crea la clave en https://myaccount.google.com/apppasswords. Se usa `smtp.gmail.com:465` con TLS y versiones HTML y texto plano, sin imágenes ni adjuntos y sin nuevas dependencias. El nombre del remitente es «Edu Lab», igual al nombre indicado para la cuenta Gmail. El código aparece en el asunto y en la primera línea visible del correo. La aceptación SMTP no confirma que Gmail lo coloque en la bandeja principal; para diagnosticar spam se necesitan los encabezados de un envío reciente. El código vence en 10 minutos, permite cinco intentos y se puede reenviar después de 60 segundos (máximo cinco envíos por correo y hora, veinte por IP y hora). La solicitud pendiente vive en la base de datos; la cookie solo lleva un identificador aleatorio y un token CSRF, sin contraseña ni código. Solicitudes con más de 24 horas se depuran al iniciar nuevos registros. Las cuentas existentes conservan su acceso. Google OAuth no envía códigos propios: solo acepta correos confirmados por Google (`email_verified`).

Las contraseñas nuevas requieren al menos seis caracteres y un segundo campo de confirmación, tanto al registrarse como al recuperarlas. «Olvidé mi contraseña» lleva a `/forgot-password`: correo → código → contraseña nueva. La recuperación utiliza el mismo Gmail, caduca y limita envíos e intentos igual que el registro; después de verificar el código hay diez minutos para completar el cambio. El código sirve una sola vez y no puede usarse para registrar cuentas. El cambio cierra las sesiones anteriores incrementando `users.auth_version`. La respuesta de recuperación no revela si el correo existe; las cuentas que solo usan Google deben continuar con Google.

El catálogo de pagos tiene tres opciones configurables desde admin: Recarga ($2,99, 20 informes sin caducidad, solo Word y sin glosarios, documentos durante 1 hora), Premium ($4,99 por 30 días, 400 generaciones, 100 términos, 72 horas) y Pro ($14,99 por 30 días, anunciado como ilimitado con un límite interno de 2000 generaciones, 300 términos, 1 año). El admin puede activar cada opción y editar precio, beneficios, cupo, términos y conservación. Desactivar una opción impide nuevas compras, sin cancelar las ya adquiridas. El cupo mensual se renueva cada 30 días; mientras haya un plan mensual activo, las recargas quedan pausadas, incluso si se agota el cupo mensual. Las generaciones fallidas devuelven su cupo. La conservación se fija al crear cada documento y no se modifica después. Los informes creados con recarga solo permiten descargar y compartir Word, incluso después de activar un plan mensual.

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

## ☁️ Producción: Supabase y Cloudflare R2

La app no necesita guardar nada en el servidor donde corre: la base de datos vive en **Supabase** (PostgreSQL) y los informes generados en **Cloudflare R2** (compatible con S3). Así se puede cambiar de hosting (Azure, DigitalOcean…) sin perder datos. Sin estas variables usa SQLite y la carpeta `output/`, que sirve para desarrollar.

1. **Supabase:** crea un proyecto y copia la cadena *Transaction pooler* (puerto 6543) en `DATABASE_URL`. Usa el pooler: la conexión directa solo habla IPv6. Las tablas se crean solas al arrancar.
2. **Cloudflare R2:** crea un bucket **privado** y un token de API con permiso *Object Read & Write* limitado a ese bucket. Rellena `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` y `R2_BUCKET`. Las descargas usan URLs firmadas de 5 minutos. Como respaldo, añade una regla de ciclo de vida que borre objetos a los 2 días; la app ya borra los vencidos cada 15 minutos.
3. **Comprueba todo con tus credenciales:** `python tools/check_services.py` conecta a la base, sube, descarga y borra un archivo de prueba.
4. **Si ya tienes usuarios en SQLite:** `python tools/migrate_sqlite_to_postgres.py gullieth.db` los copia a Supabase conservando ids y pagos (no corre si el destino ya tiene datos).

Otro proveedor S3 (AWS, DigitalOcean Spaces, Backblaze, MinIO): define `S3_ENDPOINT_URL` además de las claves.

### Pruebas

`pytest tests` corre sobre SQLite y nunca toca Supabase ni R2 (el aislamiento está en `tests/conftest.py`). Dos modos opcionales:

- `VALIDATE_PG_SQL=1 pytest tests` valida con el parser real de PostgreSQL cada consulta que ejecute la app.
- `TEST_DATABASE_URL=postgresql://... pytest tests` corre toda la suite contra un PostgreSQL de pruebas. **Borra el esquema `public` antes de cada prueba**: usa una base desechable.

## 🏛️ Logos de universidades

Cada logo vive en `static/logos/` con el nombre de la universidad en minúsculas, sin tildes y con guiones bajos (`universidad_de_los_andes.png`), y la universidad debe estar en `static/txt/lista_imagenes.txt`. Después de agregar o cambiar uno, corre:

```powershell
python tools/fix_logos.py          # arregla y crea las miniaturas
python tools/fix_logos.py --check  # solo informa, sin modificar nada
```

El script recorta los márgenes vacíos del logo (en Word todos se dibujan con el mismo alto, 3 cm, y un margen vacío haría que se vea más pequeño que los demás), limita su altura a 600 px y crea su **miniatura web** en `static/logos/thumbs/` (WebP sin pérdida, máximo 320 × 180 px, misma proporción). La landing usa solo las miniaturas: pesan ~25 KB en vez de ~180 KB y el cambio de logo de la portada de muestra no se traba. Si falta una miniatura la web usa el logo completo y todo funciona, solo que más pesado; `pytest` avisa cuando alguna falta o quedó desactualizada.

## ⏱️ Generación en segundo plano

Generar un documento puede tardar varios minutos (modelos lentos, búsquedas, bibliografía), y los proxies cortan las peticiones largas: Azure responde `504 GatewayTimeout` a los ~230 s aunque el servidor termine bien. Por eso `POST /process_form` solo valida y termina al instante; el documento se genera en un hilo (`jobs.py`) y la página `/generating/<token>` consulta su avance real (tabla `generation_jobs`), pasa sola a la descarga y, si el usuario cierra la página, el documento aparece igual en *Mis informes*.

- Un documento a la vez por usuario, y como máximo `GENERATION_MAX_CONCURRENT` (8) a la vez por proceso.
- Mientras corre, el hilo late cada 30 s. Si el proceso muere (reinicio, despliegue), a los 3 minutos el trabajo se cierra como interrumpido **y el cupo se devuelve** (una sola vez).
- El registro del servidor muestra el tiempo de cada etapa (`Generación a1b2c3d4: etapa "content" a los 12.0 s`), útil para ver dónde se va el tiempo.
- Con varias instancias funciona igual: el avance está en la base de datos, no en memoria.

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
