from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse


def _load_env(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _mask(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    p = urlparse(u)
    host = p.hostname or ""
    port = p.port or ""
    db = (p.path or "").lstrip("/")
    scheme = p.scheme or ""
    return f"{scheme}://***@{host}:{port}/{db}"


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    _load_env(root)
    for key in ["DATABASE_URL", "DATABASE_URL_POOLER"]:
        val = os.environ.get(key, "")
        print(f"{key}_set={bool(val.strip())}")
        if val.strip():
            print(f"{key}_masked={_mask(val)}")


if __name__ == "__main__":
    main()
