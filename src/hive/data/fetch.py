"""Small HTTP helper: retries, timeouts, and a descriptive User-Agent.

Uses the ``httpx`` package directly (the Anthropic SDK brings its own
``httpx2``; the two coexist). Honors ``HTTPS_PROXY`` and ``SSL_CERT_FILE``
from the environment, which is what sandboxed runners rely on.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_UA = "hive-trading-study/0.1 (+https://github.com/eliascroesus/aitrading)"


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    retries: int = 3,
    backoff: float = 1.5,
) -> httpx.Response:
    hdrs = {"User-Agent": DEFAULT_UA, **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = httpx.request(
                method, url, params=params, json=json, headers=hdrs, timeout=timeout,
                follow_redirects=True,
            )
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = backoff ** attempt
                log.warning("HTTP %s from %s; retrying in %.1fs", r.status_code, url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last = exc
            if attempt >= retries or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code not in (429, 500, 502, 503, 504)
            ):
                raise
            wait = backoff ** attempt
            log.warning("%s on %s; retrying in %.1fs", type(exc).__name__, url, wait)
            time.sleep(wait)
    assert last is not None
    raise last


def get_text(url: str, **kw: Any) -> str:
    return request("GET", url, **kw).text


def get_json(url: str, **kw: Any) -> Any:
    return request("GET", url, **kw).json()


def post_json(url: str, payload: Any, **kw: Any) -> Any:
    return request("POST", url, json=payload, **kw).json()
