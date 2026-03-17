from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(*, log_path: str = "logs/scraper.log", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("maricopa")
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    # Console
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logger.level)
    logger.addHandler(sh)

    # File
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(p, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logger.level)
    logger.addHandler(fh)

    return logger
