#!/usr/bin/env python3
"""Convenience wrapper for the Gila interval runner.

Allows running from repo root:
  python run_gila_interval.py --lookback-days 7 --ocr-limit -1 --write-files

Implementation lives in: gila/run_gila_interval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from gila.run_gila_interval import main  # noqa: E402


if __name__ == "__main__":
    main()
