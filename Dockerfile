# Imagen de producción: Flask + gunicorn + LibreOffice (para el índice y el PDF).
FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# LibreOffice sin interfaz + python3-uno (el módulo `uno` vive en /usr/bin/python3,
# que es el que usa lo_finalize.py) + fuentes: Liberation reemplaza a Arial con
# las mismas medidas, así la paginación del PDF no cambia.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui python3-uno \
        fonts-liberation fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuario sin privilegios. .lo_profile (perfil de LibreOffice) y output/ (solo si
# no hay R2 configurado) necesitan ser escribibles.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/output /app/.lo_profile \
    && chown -R app:app /app
USER app

EXPOSE 8000

# UN solo worker con varios hilos: las conversiones de LibreOffice se serializan
# con un candado dentro del proceso (Document_process._LO_LOCK) y la limpieza de
# archivos vencidos corre como un hilo; con varios workers se pisarían.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 180 --access-logfile -"]
