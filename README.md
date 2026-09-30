# Gullieth · Demo estática

Versión de demostración de [Gullieth](https://github.com/olivier-gm/GulliethAI) que corre **solo en el navegador**, sin servidor, pensada para publicarse con GitHub Pages.

## Qué funciona

- **Inicio**, **formulario** de 3 pasos con vista previa en vivo de la portada, logos de universidades y barra de progreso.
- **Generar**: simula la generación y entrega un **Word (.docx) y un PDF reales**, creados en el navegador con la portada que llenaste. En modo "Lo escribo yo" incluyen tu texto; en modo IA llevan texto de demostración (no hay IA en la demo).
- **Compartir** por WhatsApp, Gmail o el menú del dispositivo (comparte el enlace de la demo).
- **Planes**, **inicio de sesión**, **registro** y **panel admin** con datos de ejemplo: los formularios están simulados y no guardan nada.

## Qué no hace

No hay base de datos, cuentas, IA de Gemini, búsqueda en tiempo real, pagos ni conversión con LibreOffice. El índice del Word no lleva números de página.

## Publicarla

En GitHub: **Settings → Pages → Build and deployment → Deploy from a branch → `demo` / `/ (root)`**.

## Regenerarla

Esta rama se genera desde las plantillas reales de `main` con `tools/build_demo.py`. Desde la raíz de una copia de `main` (con su entorno virtual activo):

```bash
git worktree add ../gullieth-demo demo
python ../gullieth-demo/tools/build_demo.py ../gullieth-demo
```

El script vuelve a renderizar las plantillas y conserva el simulador (`static/js/demo.js`) y las librerías de `static/vendor/`.
Después haz commit y push de la rama `demo`.
