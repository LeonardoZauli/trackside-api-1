"""
Logger centralizzato — output leggibile nel Render log viewer.
Importa get_logger(name) ovunque ti serve loggare.
"""

import logging
import sys
from config import settings

_CONFIGURED = False


def _configure_root_logger() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    handler.setLevel(settings.LOG_LEVEL.upper())

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL.upper())
    root.handlers.clear()
    root.addHandler(handler)

    # Silenzia librerie verbose
    for lib in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure_root_logger()
    return logging.getLogger(name)
