"""
Route Admin — protette da X-Admin-Key header.
Tutte le operazioni sensibili: reload modello, statistiche, rate limits.

Header richiesto: X-Admin-Key: <ADMIN_API_KEY>
"""

import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from model_manager import ModelManager
from security import require_admin_key, rate_store
from config import settings
from logger import get_logger

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_key)])

_start_time = time.time()


# ──────────────────────────────────────────────────────────────
# GET /admin/status — panoramica completa
# ──────────────────────────────────────────────────────────────

@router.get("/status", summary="Stato completo dell'applicazione")
async def admin_status():
    uptime_sec = int(time.time() - _start_time)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"

    return {
        "app": {
            "env": settings.APP_ENV,
            "debug": settings.DEBUG,
            "docs_enabled": settings.ENABLE_DOCS,
            "uptime": uptime_str,
            "uptime_seconds": uptime_sec,
            "timestamp": datetime.now().isoformat(),
        },
        "model": ModelManager.info(),
        "security": {
            "cors_origins": settings.cors_origins,
            "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
            "signature_max_age_sec": settings.SIGNATURE_MAX_AGE_SECONDS,
            "active_ips_tracked": len(rate_store.all_ips()),
        },
    }


# ──────────────────────────────────────────────────────────────
# POST /admin/reload — ricarica modello senza riavvio
# ──────────────────────────────────────────────────────────────

@router.post("/reload", summary="Ricarica il modello pkl a caldo")
async def admin_reload():
    logger.info("Reload modello richiesto da admin")
    try:
        info = ModelManager.reload()
        return {"success": True, "model": info}
    except Exception as e:
        logger.error("Reload fallito: %s", e, exc_info=True)
        raise HTTPException(500, detail=f"Reload fallito: {str(e)}")


# ──────────────────────────────────────────────────────────────
# GET /admin/model — info sul modello caricato
# ──────────────────────────────────────────────────────────────

@router.get("/model", summary="Info dettagliate sul modello")
async def admin_model():
    info = ModelManager.info()
    if not info.get("loaded"):
        raise HTTPException(503, detail="Modello non caricato")

    p = ModelManager.get()
    stats = p.get_stats()
    return {"model_info": info, "predictor_stats": stats}


# ──────────────────────────────────────────────────────────────
# GET /admin/rate-limits — visualizza tutti gli IP tracciati
# ──────────────────────────────────────────────────────────────

@router.get("/rate-limits", summary="Visualizza rate limit per IP")
async def admin_rate_limits():
    return {
        "limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
        "ips": rate_store.all_ips(),
    }


# ──────────────────────────────────────────────────────────────
# DELETE /admin/rate-limits/{ip} — resetta un IP specifico
# ──────────────────────────────────────────────────────────────

@router.delete("/rate-limits/{ip}", summary="Resetta rate limit per un IP specifico")
async def admin_reset_ip(ip: str):
    reset = rate_store.reset_ip(ip)
    if not reset:
        raise HTTPException(404, detail=f"IP '{ip}' non trovato nel rate limit store")
    logger.info("Rate limit resettato per IP: %s", ip)
    return {"success": True, "ip": ip}


# ──────────────────────────────────────────────────────────────
# DELETE /admin/rate-limits — resetta tutti gli IP
# ──────────────────────────────────────────────────────────────

@router.delete("/rate-limits", summary="Resetta rate limit per tutti gli IP")
async def admin_reset_all():
    count = rate_store.reset_all()
    logger.info("Rate limit resettato per tutti gli IP (%d entries)", count)
    return {"success": True, "cleared_entries": count}


# ──────────────────────────────────────────────────────────────
# GET /admin/config — configurazione attiva (no secrets!)
# ──────────────────────────────────────────────────────────────

@router.get("/config", summary="Configurazione attiva (senza valori segreti)")
async def admin_config():
    return {
        "APP_ENV": settings.APP_ENV,
        "DEBUG": settings.DEBUG,
        "ENABLE_DOCS": settings.ENABLE_DOCS,
        "LOG_LEVEL": settings.LOG_LEVEL,
        "MODEL_PATH": settings.MODEL_PATH,
        "CSV_PATH": settings.CSV_PATH,
        "CORS_ORIGINS": settings.cors_origins,
        "RATE_LIMIT_PER_MINUTE": settings.RATE_LIMIT_PER_MINUTE,
        "SIGNATURE_MAX_AGE_SECONDS": settings.SIGNATURE_MAX_AGE_SECONDS,
        # API_SECRET e ADMIN_API_KEY non vengono mai restituiti!
        "API_SECRET": "***",
        "ADMIN_API_KEY": "***",
    }
