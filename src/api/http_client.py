"""HTTP GET with retries and a small on-disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from config import CACHE_DIR, REQUEST_RETRIES, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

# #region agent log
import threading as _th

_inflight = {"n": 0}
_inflight_lock = _th.Lock()


def _dbg(hyp: str, where: str, msg: str, data: dict) -> None:
    try:
        with open(Path(CACHE_DIR).parent.parent / "debug-90ea60.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"sessionId": "90ea60", "runId": "post-fix", "hypothesisId": hyp, "location": where, "message": msg, "data": data, "timestamp": int(time.time() * 1000)}) + "\n")
    except Exception:
        pass
# #endregion

_client: httpx.Client | None = None

# NHL's API answers a burst with 429 and "Retry-After: 60". Retrying inside that minute only adds
# rejected calls, so the host is left alone until it reopens and saved data is used meanwhile.
_LIMITED_HOSTS = ("nhle.com",)
_cooldown_until: dict[str, float] = {}
_cooldown_lock = threading.Lock()
_MISSING = object()


class RateLimited(httpx.HTTPError):
    """The host is cooling down after a 429 and no saved copy exists."""


def _limited_host(url: str) -> str | None:
    return next((host for host in _LIMITED_HOSTS if host in url), None)


def _stale_body(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["body"]
    except (OSError, ValueError, KeyError):
        return _MISSING


def _start_cooldown(host: str, retry_after: str | None) -> None:
    try:
        seconds = min(max(float(retry_after or 60), 5.0), 120.0)
    except ValueError:
        seconds = 60.0
    with _cooldown_lock:
        already = _cooldown_until.get(host, 0.0) > time.time()
        _cooldown_until[host] = max(_cooldown_until.get(host, 0.0), time.time() + seconds)
    if not already:
        logger.warning("%s is rate-limiting this computer. Using saved data for about %d seconds.", host, seconds)


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


def _redacted_target(url: str, params: dict | None) -> str:
    parts = []
    for key, value in (params or {}).items():
        shown = "***" if str(key).lower() == "apikey" else value
        parts.append(f"{key}={shown}")
    if not parts:
        return url
    return url + "?" + "&".join(parts)


def get_json(
    url: str,
    params: dict | None = None,
    *,
    ttl_seconds: int = 300,
    use_cache: bool = True,
    header_sink: dict | None = None,
    headers: dict | None = None,
    attempts: int | None = None,
) -> Any:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url, params)
    if use_cache and path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - payload.get("saved_at", 0) <= ttl_seconds:
                # #region agent log
                if "nhle.com" in url:
                    _dbg("C", "http_client.py:cache", "cache hit", {"url": url.split("nhle.com")[1][:60]})
                # #endregion
                return payload["body"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    limited = _limited_host(url)
    if limited and _cooldown_until.get(limited, 0.0) > time.time():
        stale = _stale_body(path) if use_cache else _MISSING
        # #region agent log
        _dbg("B", "http_client.py:cooldown", "skipped during cooldown", {"url": url.split("nhle.com")[1][:60], "had_saved_copy": stale is not _MISSING, "left_s": round(_cooldown_until[limited] - time.time(), 1)})
        # #endregion
        if stale is not _MISSING:
            return stale
        raise RateLimited(f"{limited} is cooling down after a 429")

    last_error: Exception | None = None
    tries = max(1, attempts or REQUEST_RETRIES)
    for attempt in range(tries):
        try:
            # #region agent log
            _nhle = "nhle.com" in url
            if _nhle:
                with _inflight_lock:
                    _inflight["n"] += 1
                    _n = _inflight["n"]
                _dbg("A", "http_client.py:request", "nhle request start", {"url": url.split("nhle.com")[1][:60], "attempt": attempt + 1, "inflight": _n, "thread": _th.current_thread().name})
            # #endregion
            try:
                response = get_client().get(url, params=params, headers=headers)
            finally:
                # #region agent log
                if _nhle:
                    with _inflight_lock:
                        _inflight["n"] -= 1
                # #endregion
            # #region agent log
            if _nhle:
                _dbg("B", "http_client.py:response", "nhle response", {"url": url.split("nhle.com")[1][:60], "status": response.status_code, "retry_after": response.headers.get("retry-after"), "ratelimit": {k: v for k, v in response.headers.items() if "ratelimit" in k.lower() or "rate-limit" in k.lower()}, "attempt": attempt + 1})
            # #endregion
            response.raise_for_status()
            body = response.json()
            if header_sink is not None:
                header_sink.clear()
                header_sink.update(response.headers)
            if use_cache:
                path.write_text(
                    json.dumps({"saved_at": time.time(), "body": body}),
                    encoding="utf-8",
                )
            return body
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429 and limited:
                _start_cooldown(limited, getattr(getattr(exc, "response", None), "headers", {}).get("retry-after"))
                stale = _stale_body(path) if use_cache else _MISSING
                # #region agent log
                _dbg("B", "http_client.py:429", "429 handled by cooldown", {"url": url.split("nhle.com")[1][:60], "had_saved_copy": stale is not _MISSING, "attempt": attempt + 1})
                # #endregion
                if stale is not _MISSING:
                    return stale
                break
            logger.warning(
                "GET %s failed (%s/%s): status %s",
                _redacted_target(url, params),
                attempt + 1,
                tries,
                status or type(exc).__name__,
            )
            if status is not None and status != 429 and 400 <= status < 500:
                break
            if attempt + 1 < tries:
                time.sleep(0.4 * (2**attempt))
    assert last_error is not None
    raise last_error
