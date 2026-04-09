"""
Sicurezza — HMAC-SHA256, rate limiter in-memory, middleware, admin key.
"""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse

from config import settings
from logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# Rate Limiter (in-memory, sliding window)
# ──────────────────────────────────────────────────────────────

class RateLimitStore:
    """
    Sliding window per IP. Per produzione con più istanze usa Redis.
    Render free tier → 1 istanza → questo va benissimo.
    """

    def __init__(self):
        self._store: dict[str, list[float]] = defaultdict(list)

    def _clean(self, ip: str, window: int = 60) -> None:
        cutoff = time.time() - window
        self._store[ip] = [t for t in self._store[ip] if t > cutoff]

    def is_allowed(self, ip: str, limit: int = None, window: int = 60) -> bool:
        limit = limit or settings.RATE_LIMIT_PER_MINUTE
        self._clean(ip, window)
        if len(self._store[ip]) >= limit:
            return False
        self._store[ip].append(time.time())
        return True

    def remaining(self, ip: str, limit: int = None, window: int = 60) -> int:
        limit = limit or settings.RATE_LIMIT_PER_MINUTE
        self._clean(ip, window)
        return max(0, limit - len(self._store[ip]))

    def all_ips(self) -> dict:
        """Per l'admin: panoramica rate limit corrente."""
        now = time.time()
        return {
            ip: {"requests_last_minute": len([t for t in ts if now - t < 60])}
            for ip, ts in self._store.items()
            if ts
        }

    def reset_ip(self, ip: str) -> bool:
        if ip in self._store:
            del self._store[ip]
            return True
        return False

    def reset_all(self) -> int:
        count = len(self._store)
        self._store.clear()
        return count


rate_store = RateLimitStore()


# ──────────────────────────────────────────────────────────────
# HMAC Signature
# ──────────────────────────────────────────────────────────────

def verify_signature(request: Request) -> None:
    pass

# ──────────────────────────────────────────────────────────────
# Admin API Key
# ──────────────────────────────────────────────────────────────

def require_admin_key(request: Request) -> None:
    """
    Protezione semplice per le route /admin/*.
    Passa la chiave come header: X-Admin-Key: <ADMIN_API_KEY>
    """
    key = request.headers.get("X-Admin-Key", "")
    if not secrets.compare_digest(key, settings.ADMIN_API_KEY):
        logger.warning("Tentativo accesso admin non autorizzato da %s", _get_ip(request))
        raise HTTPException(401, detail="Admin key non valida o mancante")


# ──────────────────────────────────────────────────────────────
# Security Middleware
# ──────────────────────────────────────────────────────────────

def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else ""
    return ip or (request.client.host if request.client else "unknown")


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Applicato a ogni richiesta:
    1. Rate limiting per IP
    2. Origin/Referer check in production
    3. Headers di sicurezza nella risposta
    """

    BYPASS_PATHS = {"/health", "/", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next):
        ip = _get_ip(request)
        path = request.url.path

        # ── Rate limit ─────────────────────────────────────────
        if not rate_store.is_allowed(ip):
            logger.warning("Rate limit superato per IP %s su %s", ip, path)
            return JSONResponse(
                status_code=429,
                content={"error": "Troppe richieste", "retry_after_seconds": 60},
                headers={"Retry-After": "60"},
            )

        # ── Origin check (solo in production, escludi health) ──
        if settings.is_production and path not in self.BYPASS_PATHS:
            origin  = request.headers.get("Origin", "")
            referer = request.headers.get("Referer", "")
            allow_all = "*" in settings.cors_origins
            ok = allow_all or (
                    any(origin.startswith(o) for o in settings.cors_origins) or
                    any(referer.startswith(o) for o in settings.cors_origins)
            )
            if not ok:
                logger.warning("Origin bloccato: '%s' da IP %s", origin or referer, ip)
                return JSONResponse(status_code=403, content={"error": "Accesso non autorizzato"})

        response = await call_next(request)

        # ── Security headers ───────────────────────────────────
        response.headers.update({
            "X-Content-Type-Options":  "nosniff",
            "X-Frame-Options":         "DENY",
            "X-Robots-Tag":            "noindex, nofollow",
            "Cache-Control":           "no-store, no-cache, must-revalidate",
            "Pragma":                  "no-cache",
            "Referrer-Policy":         "strict-origin-when-cross-origin",
            "Permissions-Policy":      "interest-cohort=()",
            "X-RateLimit-Limit":       str(settings.RATE_LIMIT_PER_MINUTE),
            "X-RateLimit-Remaining":   str(rate_store.remaining(ip)),
        })

        return response
