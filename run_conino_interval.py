#!/usr/bin/env python3
"""Convenience wrapper for the Coconino interval runner.

Allows running from repo root:
  python run_conino_interval.py --lookback-days 14 --ocr-limit -1 --write-files --workers 2 --once

Implementation lives in: conino/run_conino_interval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from conino.run_conino_interval import main  # noqa: E402


if __name__ == "__main__":
    main()
