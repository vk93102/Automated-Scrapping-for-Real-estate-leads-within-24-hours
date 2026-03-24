from __future__ import annotations

from pathlib import Path


def main() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    print(f"env_path={env_path}")
    print(f"env_exists={env_path.exists()}")
    if not env_path.exists():
        return

    keys: set[str] = set()
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _ = s.split("=", 1)
        keys.add(k.strip())

    for k in ["DATABASE_URL", "DATABASE_URL_POOLER", "GROQ_LLM_ENDPOINT_URL", "GROQ_API_KEY", "GROQ_MODEL"]:
        print(f"has_{k}={k in keys}")


if __name__ == "__main__":
    main()
