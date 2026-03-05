from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(dotenv_path: str) -> None:
    """Tiny .env loader (no external dependency).

    Loads KEY=VALUE pairs into os.environ only if missing.
    """

    p = Path(dotenv_path)
    if not p.exists() or not p.is_file():
        return

    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        return
