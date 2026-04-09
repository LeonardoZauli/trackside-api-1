"""
ModelManager — singleton che gestisce il ciclo di vita del LidlPredictor.

Strategia di caricamento:
  1. Tenta di caricare dal file .pkl (istantaneo, dati pre-analizzati)
  2. Se il pkl non esiste, carica dal CSV e salva il pkl per i prossimi avvii
"""

import pickle
import time
from pathlib import Path
from typing import Optional

from config import settings
from logger import get_logger

logger = get_logger(__name__)


class ModelManager:
    _predictor = None
    _load_time: Optional[float] = None
    _source: Optional[str] = None     # "pkl" oppure "csv"
    _pkl_size_kb: float = 0.0

    # ──────────────────────────────────────────────────────────
    @classmethod
    def load(cls) -> None:
        pkl_path = Path(settings.MODEL_PATH)
        csv_path = Path(settings.CSV_PATH)

        t0 = time.perf_counter()

        if pkl_path.exists():
            cls._predictor = cls._load_from_pkl(pkl_path)
            cls._source = "pkl"
            cls._pkl_size_kb = round(pkl_path.stat().st_size / 1024, 1)
        elif csv_path.exists():
            logger.warning("PKL non trovato — carico dal CSV (più lento)...")
            cls._predictor = cls._load_from_csv(csv_path)
            cls._source = "csv"
            cls._save_pkl(pkl_path)
        else:
            raise FileNotFoundError(
                f"Né il modello pkl ({pkl_path}) né il CSV ({csv_path}) sono stati trovati. "
                "Assicurati di copiare i file nella cartella corretta."
            )

        cls._load_time = time.perf_counter() - t0
        logger.info(
            "Predictor caricato da [%s] in %.2fs | %d prodotti | %d date",
            cls._source.upper(),
            cls._load_time,
            len(cls._predictor.rows),
            len(cls._predictor.all_dates),
        )

    # ──────────────────────────────────────────────────────────
    @classmethod
    def _load_from_pkl(cls, path: Path):
        with open(path, "rb") as f:
            return pickle.load(f)

    @classmethod
    def _load_from_csv(cls, path: Path):
        # Import locale per non avere dipendenza circolare
        from lidl_predictor import LidlPredictor
        return LidlPredictor(str(path))

    @classmethod
    def _save_pkl(cls, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump(cls._predictor, f)
            logger.info("Modello salvato come pkl: %s", path)
        except Exception as e:
            logger.warning("Impossibile salvare pkl: %s", e)

    # ──────────────────────────────────────────────────────────
    @classmethod
    def reload(cls) -> dict:
        """Ricarica il modello a caldo (usato dall'admin)."""
        logger.info("Reload modello richiesto...")
        cls._predictor = None
        cls.load()
        return cls.info()

    @classmethod
    def unload(cls) -> None:
        cls._predictor = None
        logger.info("Predictor scaricato dalla memoria")

    # ──────────────────────────────────────────────────────────
    @classmethod
    def get(cls):
        if cls._predictor is None:
            raise RuntimeError("Predictor non caricato — controlla i log di avvio")
        return cls._predictor

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._predictor is not None

    @classmethod
    def info(cls) -> dict:
        if not cls.is_loaded():
            return {"loaded": False}
        p = cls._predictor
        return {
            "loaded": True,
            "source": cls._source,
            "load_time_sec": round(cls._load_time or 0, 3),
            "pkl_size_kb": cls._pkl_size_kb,
            "total_products": len(p.rows),
            "total_dates": len(p.all_dates),
            "data_range": f"{p.all_dates[0]} → {p.all_dates[-1]}",
        }
