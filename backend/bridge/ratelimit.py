"""Client-side token buckets, set deliberately below the nginx zones.

nginx (docs/deployment/nginx/amanahtrader.uk.conf) allows 20 r/m burst 5-10 on the
write zone and 240 r/m burst 60 on the general zone, per IP. Sitting well under those
means a 429 is a bug in this client rather than normal operation, and it leaves the
whole allowance free for ad-hoc questions.

The real constraint is not the rate limit, though -- it is CPU. Every request costs a
260,000-iteration PBKDF2 verification on a 2 GB droplet (backend/auth.py:51), because
there is no session or token. A chatty poller hurts the box long before it trips nginx.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from bridge.routes import ZONE_GENERAL, ZONE_WRITE

# (refill per minute, burst capacity). nginx allows 20/5-10 and 240/60.
ZONE_BUDGETS: dict[str, tuple[float, float]] = {
    ZONE_WRITE: (12.0, 4.0),
    ZONE_GENERAL: (120.0, 20.0),
}


@dataclass
class TokenBucket:
    """A monotonic-clock token bucket. ``acquire`` blocks until a token is free."""

    rate_per_minute: float
    capacity: float
    _tokens: float = 0.0
    _updated_at: float = 0.0

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_minute / 60.0)
        self._updated_at = now

    def acquire(self, *, timeout: float = 60.0) -> bool:
        """Take one token, waiting up to ``timeout`` seconds. False if it never freed."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._refill(now)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                shortfall = 1.0 - self._tokens
                wait = shortfall * 60.0 / self.rate_per_minute
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 1.0))


class ZoneLimiter:
    """One bucket per nginx zone, shared across every call in this process."""

    def __init__(self, budgets: dict[str, tuple[float, float]] | None = None) -> None:
        source = budgets if budgets is not None else ZONE_BUDGETS
        self._buckets = {
            zone: TokenBucket(rate_per_minute=rate, capacity=burst)
            for zone, (rate, burst) in source.items()
        }

    def acquire(self, zone: str, *, timeout: float = 60.0) -> bool:
        bucket = self._buckets.get(zone)
        if bucket is None:  # fail closed: an unknown zone is not a free pass
            raise KeyError(f"unknown rate-limit zone '{zone}'")
        return bucket.acquire(timeout=timeout)
