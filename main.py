"""
Lidl Parkside Predictor — Production API v6
============================================

Entry point principale. Avvia con:
    uvicorn main:app --host 0.0.0.0 --port 8000

Su Render.com viene avviato automaticamente tramite il comando in render.yaml.

All'avvio:
  1. Carica il modello dal .pkl (istantaneo)
  2. Registra middleware sicurezza
  3. Registra tutte le route
  4. Serve le richieste
"""

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from logger import get_logger
from model_manager import ModelManager
from security import SecurityMiddleware
from routes import health, predictions, admin

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# LIFESPAN — carica il modello all'avvio, libera alla chiusura
# ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Avvio Lidl Predictor API  |  ENV=%s", settings.APP_ENV)
    logger.info("=" * 60)

    try:
        ModelManager.load()
    except Exception as e:
        logger.critical("Avvio fallito — impossibile caricare il modello: %s", e, exc_info=True)
        raise RuntimeError(f"Avvio fallito: {e}") from e

    if settings.API_SECRET == "CAMBIAMI-con-una-chiave-segreta-lunga":
        logger.warning("Stai usando l'API_SECRET di default! Cambiala prima del deploy.")
    if settings.ADMIN_API_KEY == "CAMBIAMI-admin-key-segreta":
        logger.warning("Stai usando l'ADMIN_API_KEY di default! Cambiala prima del deploy.")

    logger.info("Modello caricato — API pronta")
    logger.info("Docs: %s", "/docs" if settings.ENABLE_DOCS else "disabilitate (production)")
    logger.info("CORS origins: %s", settings.cors_origins)

    yield  # l'app serve le richieste

    logger.info("Spegnimento — pulizia risorse...")
    ModelManager.unload()


# ──────────────────────────────────────────────────────────────
# APP
# ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Lidl Parkside Predictor API",
    version="6.0.0",
    description="Previsioni prodotti Lidl Parkside basate su pattern storici",
    docs_url="/docs"        if settings.ENABLE_DOCS else None,
    redoc_url="/redoc"      if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
    lifespan=lifespan,
)


# ──────────────────────────────────────────────────────────────
# MIDDLEWARE (ordine: CORS → Security → Request Logger)
# ──────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET"],
    allow_headers=["X-Signature", "X-Timestamp", "X-Admin-Key", "Content-Type"],
    allow_credentials=False,
    max_age=3600,
)

app.add_middleware(SecurityMiddleware)


@app.middleware("http")
async def request_logger(request: Request, call_next):
    rid = str(uuid.uuid4())[:8]
    request.state.request_id = rid
    t0 = time.perf_counter()

    logger.info("[%s] -> %s %s | %s", rid, request.method, request.url.path,
                request.client.host if request.client else "?")

    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    logger.info("[%s] <- %d | %.1fms", rid, response.status_code, elapsed_ms)
    response.headers["X-Request-ID"]    = rid
    response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
    return response


# ──────────────────────────────────────────────────────────────
# EXCEPTION HANDLERS GLOBALI
# ──────────────────────────────────────────────────────────────

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    rid = getattr(request.state, "request_id", "?")
    logger.warning("[%s] ValueError su %s: %s", rid, request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content={"error": "Dati non validi", "detail": str(exc), "request_id": rid},
    )


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError):
    rid = getattr(request.state, "request_id", "?")
    logger.error("[%s] RuntimeError su %s: %s", rid, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=503,
        content={"error": "Servizio temporaneamente non disponibile", "request_id": rid},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "?")
    logger.error("[%s] Errore non gestito su %s: %s", rid, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Errore interno del server", "request_id": rid},
    )


# ──────────────────────────────────────────────────────────────
# ROUTER
# ──────────────────────────────────────────────────────────────

app.include_router(health.router,                          tags=["Health"])
app.include_router(predictions.router, prefix="/api/v1",  tags=["Previsioni"])
app.include_router(admin.router,       prefix="/admin",   tags=["Admin"])
