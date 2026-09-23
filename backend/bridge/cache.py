"""A bounded TTL cache with single-flight, in front of the deployed API.

Two jobs, and the second matters more than the first:

1. TTL caching, so a verdict that changes only when the SC publishes is not re-fetched
   on every question.
2. Single-flight, so a chief-of-staff fan-out to five specialists all asking about the
   same ticker costs one PBKDF2 verification on the droplet rather than five.

Modelled on news_summarizer._SUMMARY_CACHE -- a plain dict, for the same reason: losing
it on restart costs one extra request, so persistence would buy nothing and add a file
to reason about.

Two rules carried over from sec_edgar_cache.py's docstring, for the same reasons:
never cache a POST, and never cache a failure. A 5xx is a fact about the droplet at
that moment, not about the company being asked after.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

MAX_ENTRIES = 512


@dataclass
class _Entry:
    value: dict
    expires_at: float


@dataclass
class ResponseCache:
    """TTL cache keyed by (route name, path params, query), with single-flight."""

    max_entries: int = MAX_ENTRIES
    _entries: dict[str, _Entry] = field(default_factory=dict)
    _inflight: dict[str, threading.Event] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def get(self, key: str, *, now: float | None = None) -> dict | None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= moment:
                self._entries.pop(key, None)
                return None
            return entry.value

    def put(self, key: str, value: dict, *, ttl_seconds: float, now: float | None = None) -> None:
        if ttl_seconds <= 0:
            return
        moment = time.monotonic() if now is None else now
        with self._lock:
            if len(self._entries) >= self.max_entries:
                # Evict the soonest-to-expire rather than an arbitrary key, so a burst
                # of one-off lookups cannot flush the long-lived verdicts.
                oldest = min(self._entries, key=lambda k: self._entries[k].expires_at)
                self._entries.pop(oldest, None)
            self._entries[key] = _Entry(value=value, expires_at=moment + ttl_seconds)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def begin_flight(self, key: str) -> tuple[bool, threading.Event]:
        """Claim the right to fetch ``key``.

        Returns (is_leader, event). The leader performs the request and must call
        ``end_flight``; followers wait on the event and then re-read the cache.
        """
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                return False, existing
            event = threading.Event()
            self._inflight[key] = event
            return True, event

    def end_flight(self, key: str) -> None:
        with self._lock:
            event = self._inflight.pop(key, None)
        if event is not None:
            event.set()


def cache_key(route_name: str, path_params: dict | None, query: dict | None) -> str:
    """Stable key. Sorted so two callers spelling a query differently still collide."""
    parts = [route_name]
    for label, mapping in (("p", path_params), ("q", query)):
        if mapping:
            rendered = ",".join(f"{k}={mapping[k]}" for k in sorted(mapping))
            parts.append(f"{label}:{rendered}")
    return "|".join(parts)
