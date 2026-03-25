#!/usr/bin/env python3
"""Convenience wrapper for the Gila Supabase DB monitor.

Allows running from repo root:
  python gila_db_monitor.py --days 14 --show-leads 20

Implementation lives in: gila/db_monitor.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from gila.db_monitor import main  # noqa: E402


if __name__ == "__main__":
    main()
