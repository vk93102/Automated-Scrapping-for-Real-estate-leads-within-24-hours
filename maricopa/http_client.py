from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class RetryConfig:
    attempts: int = 3
    base_sleep_s: float = 1.0
    max_sleep_s: float = 10.0


def _sleep_backoff(cfg: RetryConfig, attempt_index: int) -> None:
    # attempt_index is 0-based
    sleep_s = min(cfg.max_sleep_s, cfg.base_sleep_s * (2**attempt_index))
    sleep_s = sleep_s * random.uniform(0.8, 1.2)
    time.sleep(float(sleep_s))


def with_retry(
    fn: Callable[[], requests.Response],
    *,
    cfg: RetryConfig,
    retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Response:
    last_exc: Optional[Exception] = None
    for i in range(cfg.attempts):
        try:
            resp = fn()
            if resp.status_code in retry_on_status:
                if i < cfg.attempts - 1:
                    _sleep_backoff(cfg, i)
                    continue
            return resp
        except Exception as e:
            last_exc = e
            if i < cfg.attempts - 1:
                _sleep_backoff(cfg, i)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("retry: unreachable")


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s
