"""
Configurazione centralizzata — legge da variabili d'ambiente / .env
Tutte le impostazioni dell'app passano da qui.
"""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────
    APP_ENV: str = "production"
    DEBUG: bool = False
    ENABLE_DOCS: bool = False           # True solo in dev!
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000

    # ── Modello / Dati ────────────────────────────────────────
    MODEL_PATH: str = "models/lidl_predictor_v4.pkl"
    CSV_PATH: str = "data/lidl_prodotti.csv"   # fallback se pkl manca

    # ── Sicurezza pubblica (HMAC) ─────────────────────────────
    API_SECRET: str = "CAMBIAMI-con-una-chiave-segreta-lunga"
    SIGNATURE_MAX_AGE_SECONDS: int = 60
    RATE_LIMIT_PER_MINUTE: int = 60

    # ── Sicurezza admin ───────────────────────────────────────
    ADMIN_API_KEY: str = "CAMBIAMI-admin-key-segreta"

    # ── CORS ──────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()
