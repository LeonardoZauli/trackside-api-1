# ─────────────────────────────────────────────────────────────────
# Dockerfile — Lidl Predictor API v6
# Ottimizzato per Render.com (free tier)
# ─────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Metadata
LABEL maintainer="trackside-backend"
LABEL description="Lidl Parkside Predictor API v6"

# Variabili d'ambiente Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Installa dipendenze (layer cachato se requirements non cambia)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice
COPY . .

# Crea directory per modello e dati (se non presenti)
RUN mkdir -p models data

# Utente non-root per sicurezza
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
USER appuser

# Esponi la porta (Render usa la variabile PORT)
EXPOSE 8000

# Healthcheck — Render lo usa per sapere quando l'app è pronta
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Avvio
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level warning"]
