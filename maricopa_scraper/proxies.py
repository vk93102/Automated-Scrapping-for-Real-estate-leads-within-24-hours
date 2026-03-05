from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _parse_proxy_line(line: str) -> Optional[str]:
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None

    # Accept either full URL or webshare-style host:port:user:pass
    if "://" in line:
        return line

    parts = line.split(":")
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"

    return None


@dataclass(frozen=True)
class ProxyProvider:
    proxies: tuple[str, ...]

    @classmethod
    def from_file(cls, path: str) -> "ProxyProvider":
        p = Path(path)
        if not p.exists():
            return cls(proxies=tuple())

        out: list[str] = []
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            u = _parse_proxy_line(ln)
            if u:
                out.append(u)
        return cls(proxies=tuple(out))

    def pick(self) -> Optional[str]:
        if not self.proxies:
            return None
        return random.choice(self.proxies)

    def as_requests_proxies(self) -> Optional[dict[str, str]]:
        p = self.pick()
        if not p:
            return None
        return {"http": p, "https": p}
