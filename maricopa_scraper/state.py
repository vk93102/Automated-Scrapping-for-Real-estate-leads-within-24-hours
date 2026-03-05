from __future__ import annotations

from pathlib import Path


def load_seen(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    out: set[str] = set()
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = (ln or "").strip()
        if ln and ln.isdigit():
            out.add(ln)
    return out


def append_seen(path: str, recording_number: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(str(recording_number).strip() + "\n")
