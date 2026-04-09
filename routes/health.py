from fastapi import APIRouter
from fastapi.responses import JSONResponse
from model_manager import ModelManager

router = APIRouter()


@router.get("/health", summary="Health check base — usato da Render")
async def health():
    loaded = ModelManager.is_loaded()
    return JSONResponse(
        status_code=200 if loaded else 503,
        content={"status": "ok" if loaded else "degraded", "model_loaded": loaded},
    )


@router.get("/health/full", summary="Health check dettagliato")
async def health_full():
    info = ModelManager.info()
    ok = info.get("loaded", False)
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "model": info},
    )
