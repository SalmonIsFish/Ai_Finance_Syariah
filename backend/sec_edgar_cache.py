"""TEMPORARY SHIM -- a flat-file TTL cache and request throttle for SEC EDGAR.

**This is deliberately not the screening store.** ``NEXT_STEPS.md`` specifies an
append-only ``shariah_screens`` table plus a raw EDGAR cache, so that a verdict
can be dated, compared against the previous verdict, and used to detect the
moment a company stops being compliant. None of that is here. This module only
answers one narrow question -- *"have we already downloaded this exact URL
recently?"* -- so that re-recording a demo does not re-download several megabytes
per take and does not push past SEC's ~10 requests/second guidance.

Replace it with the real store after submission. When that lands, delete this
file; nothing depends on its shape except ``sec_edgar_screen.sec_request``.

What it deliberately does **not** do:

- It does not store verdicts, only raw responses. A verdict is a *decision* and
  belongs in an auditable, dated table, not in a cache that expires.
- It does not cache failures. A 404 or a timeout says something about SEC, not
  about the company, and ``sec_edgar_screen`` fails closed on ERROR -- so caching
  one bad request would make a symbol look unscreenable for the whole TTL.
- It does not invalidate on new filings. The TTL is a blunt instrument; checking
  the ``submissions`` endpoint for a newer filing is the correct answer and is
  part of the real store's design.

Configuration (both read from ``backend/.env`` via the environment):

| variable | default | meaning |
|---|---|---|
| ``SEC_EDGAR_CACHE_TTL_HOURS`` | ``24`` | entry lifetime; ``0`` disables the cache |
| ``SEC_EDGAR_MIN_REQUEST_INTERVAL_MS`` | ``125`` | floor between live fetches (~8/s) |
| ``SEC_EDGAR_CACHE_DIR`` | ``backend/sec_edgar_cache`` | where entries live; read at import |
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from config import BACKEND_DIR


# run_all_tests.py points this at an empty directory for every run. A warm
# developer cache once kept three files green that fetched from SEC on a miss: the
# census passed within 24h of a live run and failed as NETWORK after (2026-09-30).
CACHE_DIR = Path(os.getenv("SEC_EDGAR_CACHE_DIR") or BACKEND_DIR / "sec_edgar_cache")

DEFAULT_TTL_HOURS = 24.0
# SEC's published guidance is a maximum of 10 requests/second. 125 ms is ~8/s,
# which leaves headroom for the fact that this process is not the only thing on
# the machine that might be talking to SEC.
DEFAULT_MIN_REQUEST_INTERVAL_MS = 125.0

_last_request_at: float | None = None


def reset_throttle() -> None:
    """Forget when the last live fetch happened (tests, long-running processes)."""
    global _last_request_at
    _last_request_at = None


def throttle(*, min_interval_seconds: float | None = None, now=None, sleep=None) -> None:
    """Block until at least ``min_interval_seconds`` has passed since the last fetch."""
    global _last_request_at
    now = now or time.monotonic
    sleep = sleep or time.sleep
    if min_interval_seconds is None:
        min_interval_seconds = _configured_min_interval_seconds()

    current = now()
    if _last_request_at is not None and min_interval_seconds > 0:
        elapsed = current - _last_request_at
        if elapsed < min_interval_seconds:
            sleep(min_interval_seconds - elapsed)
            current = now()
    _last_request_at = current


def cached_fetch(
    url: str, fetch, *, cache_dir=None, ttl_seconds: float | None = None, now=None
) -> dict:
    """Return ``fetch()``'s response, serving a recent identical URL from disk.

    ``fetch`` is a zero-argument callable returning the ``sec_request`` response
    shape (``ok``/``status_code``/``data``). It is invoked only on a miss, which
    is what the tests assert on -- the point of the shim is the call that does
    *not* happen.

    The returned dict carries a ``cache`` key (``hit``/``miss``/``disabled``) so
    a caller can tell where an answer came from without guessing.
    """
    now = now or time.time
    cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    if ttl_seconds is None:
        ttl_seconds = _configured_ttl_seconds()

    if ttl_seconds <= 0:
        throttle()
        return {**fetch(), "cache": "disabled"}

    path = _entry_path(cache_dir, url)
    entry = _read_entry(path)
    if entry is not None and now() - entry["cached_at"] < ttl_seconds:
        return {
            "ok": True,
            "status_code": entry.get("status_code", 200),
            "data": entry["data"],
            "cache": "hit",
            "cached_at": entry["cached_at"],
        }

    throttle()
    response = fetch()
    # Only a successful response is durable enough to reuse; see the module docstring.
    if response.get("ok"):
        _write_entry(path, url=url, response=response, cached_at=now())
        return {**response, "cache": "miss"}

    # The live fetch failed. If ``entry`` is set here it is necessarily expired
    # (a fresh one would have returned above), but it is still a real prior
    # answer from SEC -- serving it, clearly marked stale, beats failing the
    # whole screen closed over what is often a transient block (rate limit,
    # shared egress IP) rather than a fact about the company. Never rewritten:
    # its cached_at must keep reporting genuine staleness.
    if entry is not None:
        return {
            "ok": True,
            "status_code": entry.get("status_code", 200),
            "data": entry["data"],
            "cache": "stale",
            "cached_at": entry["cached_at"],
            "stale_age_hours": (now() - entry["cached_at"]) / 3600.0,
            "live_fetch_reason": response.get("reason"),
        }
    return {**response, "cache": "miss"}


def _entry_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return cache_dir / f"{digest}.json"


def _read_entry(path: Path) -> dict | None:
    """Return a usable entry, or None if it is absent, corrupt, or malformed."""
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or "data" not in entry:
        return None
    cached_at = entry.get("cached_at")
    if not isinstance(cached_at, (int, float)):
        return None
    return {**entry, "cached_at": float(cached_at)}


def _write_entry(path: Path, *, url: str, response: dict, cached_at: float) -> None:
    """Best-effort write. A cache that cannot be written must not break a screen."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": url,
            "cached_at": cached_at,
            "status_code": response.get("status_code", 200),
            "data": response.get("data"),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def _configured_ttl_seconds() -> float:
    return _env_float("SEC_EDGAR_CACHE_TTL_HOURS", DEFAULT_TTL_HOURS) * 3600.0


def _configured_min_interval_seconds() -> float:
    return _env_float("SEC_EDGAR_MIN_REQUEST_INTERVAL_MS", DEFAULT_MIN_REQUEST_INTERVAL_MS) / 1000.0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default
