"""HTTP GET with retries and a small on-disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from config import CACHE_DIR, REQUEST_RETRIES, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    return _client


def _cache_path(url: str, params: dict | None) -> Path:
    raw = url + "?" + json.dumps(params or {}, sort_keys=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return CACHE_DIR / f"{digest}.json"


def get_json(
    url: str,
    params: dict | None = None,
    *,
    ttl_seconds: int = 300,
    use_cache: bool = True,
) -> Any:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url, params)
    if use_cache and path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - payload.get("saved_at", 0) <= ttl_seconds:
                return payload["body"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = get_client().get(url, params=params)
            response.raise_for_status()
            body = response.json()
            if use_cache:
                path.write_text(
                    json.dumps({"saved_at": time.time(), "body": body}),
                    encoding="utf-8",
                )
            return body
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("GET %s failed (%s/%s): %s", url, attempt + 1, REQUEST_RETRIES, exc)
            time.sleep(0.4 * (2**attempt))
    assert last_error is not None
    raise last_error
