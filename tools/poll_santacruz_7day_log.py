from __future__ import annotations

import time
from pathlib import Path


def main() -> int:
    log_path = Path("logs/santacruz_interval.log")
    marker = "starting santacruz one-shot runner lookback_days=7"

    max_iters = 200
    sleep_seconds = 15

    for _ in range(max_iters):
        if not log_path.exists():
            print("log missing")
            time.sleep(5)
            continue

        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start_index = 0
        for i in range(len(lines) - 1, -1, -1):
            if marker in lines[i]:
                start_index = i
                break

        tail = lines[start_index:]
        for ln in reversed(tail):
            if "run ok total=" in ln or "run failed" in ln:
                print(ln)
                return 0

        if lines:
            print(lines[-1])

        time.sleep(sleep_seconds)

    print("TIMEOUT")
    print("\n".join(lines[-50:]))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
