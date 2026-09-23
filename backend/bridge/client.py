"""The one HTTP client for the deployed Amanah Trader API.

Design rules, each of which a test asserts:

* **No public method accepts a URL.** ``call()`` takes a route *name* from routes.ROUTES.
  Route invention is blocked in Python, not merely in the MCP tool schema.
* **The operator header is attached in exactly one place**, by a function that raises
  for any route outside routes.OPERATOR_ROUTES. It is the only thing standing between
  this process and the broker, so it is not an ``if`` scattered through the call path.
* **``_request`` is the single swappable seam**, with the same contract as
  alpaca_paper_adapter.alpaca_request and news_summarizer.openrouter_request: never
  raises, always returns {"ok", "status_code", "data", "reason"?}. Tests swap it; they
  never reach the network.
* **Execute is never retried.** A retry that "helpfully" resends a submission is how you
  get two orders. A timeout there returns UNKNOWN, and a human must go and look.

stdlib only -- backend/requirements.txt has no ``requests``.
"""

from __future__ import annotations

import base64
import json
import os
import random
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bridge.cache import ResponseCache, cache_key
from bridge.ratelimit import ZoneLimiter
from bridge.routes import OPERATOR_ROUTES, Route, route_for
from bridge.sanitize import sanitize_json

DEFAULT_BASE_URL = "https://amanahtrader.uk"
REQUEST_TIMEOUT_SECONDS = 20
EXECUTE_TIMEOUT_SECONDS = 45

OPERATOR_HEADER = "X-Amanah-Operator"

MAX_ATTEMPTS = 3
BACKOFF_CAP_SECONDS = 30.0
RETRY_STATUS = frozenset({429, 502, 503, 504})

STATUS_OK = "OK"
STATUS_RATE_LIMITED = "RATE_LIMITED"
STATUS_AUTH_FAILED = "AUTH_FAILED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_UNKNOWN = "UNKNOWN"


class OperatorKeyMisuse(RuntimeError):
    """Raised when the operator key would reach a route that must not carry it."""


@dataclass(frozen=True)
class BridgeConfig:
    """Credentials for one bridge process.

    ``operator_key`` is None in the MCP (LLM-facing) process. That is the whole
    process-separation argument: the key is not merely unused there, it is absent.
    """

    base_url: str
    basic_user: str
    basic_password: str
    operator_key: str | None = None
    user_agent: str = "amanah-bridge/0.1"

    def __repr__(self) -> str:
        # Never let a secret reach a log, a traceback, or an MCP error payload.
        operator = "<redacted>" if self.operator_key else None
        return (
            f"BridgeConfig(base_url={self.base_url!r}, basic_user={self.basic_user!r}, "
            f"basic_password=<redacted>, operator_key={operator!r}, "
            f"user_agent={self.user_agent!r})"
        )

    __str__ = __repr__


def load_bridge_config(*, with_operator: bool = False, env: dict | None = None) -> BridgeConfig:
    """Read credentials from the environment, failing closed on anything missing.

    ``with_operator`` is passed True only by relay.py. mcp_server.py never calls it that
    way, which is what keeps the operator key out of the LLM-facing process entirely.
    """
    source = os.environ if env is None else env
    user = (source.get("BRIDGE_BASIC_USER") or "").strip()
    password = source.get("BRIDGE_BASIC_PASSWORD") or ""
    if not user or not password:
        raise RuntimeError(
            "BRIDGE_BASIC_USER and BRIDGE_BASIC_PASSWORD must be set "
            "(see docs/deployment/openclaw-bridge.md)"
        )
    operator_key = None
    if with_operator:
        operator_key = (source.get("BRIDGE_OPERATOR_KEY") or "").strip() or None
        if operator_key is None:
            raise RuntimeError(
                "BRIDGE_OPERATOR_KEY must be set for the relay process; without it "
                "/paper/execute returns 401 at nginx"
            )
    base_url = (source.get("BRIDGE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return BridgeConfig(
        base_url=base_url,
        basic_user=user,
        basic_password=password,
        operator_key=operator_key,
    )


def _operator_header_for(route: Route, config: BridgeConfig) -> dict[str, str]:
    """The ONLY place the operator key is attached to a request.

    Raises for any route outside OPERATOR_ROUTES, so a future edit that points a new
    route at the key fails loudly rather than silently widening broker access.
    """
    if not route.needs_operator:
        return {}
    if route.name not in OPERATOR_ROUTES:
        raise OperatorKeyMisuse(
            f"route '{route.name}' asks for the operator key but is not in OPERATOR_ROUTES"
        )
    if not config.operator_key:
        raise OperatorKeyMisuse(
            f"route '{route.name}' requires the operator key, but this process has none "
            "(the MCP process is built without it on purpose)"
        )
    return {OPERATOR_HEADER: config.operator_key}


def _basic_header(config: BridgeConfig) -> dict[str, str]:
    raw = f"{config.basic_user}:{config.basic_password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _decode_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _request(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> dict:
    """The seam every test swaps. Never raises; mirrors alpaca_request's contract."""
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return {
                "ok": True,
                "status_code": response.status,
                "data": _decode_json(response.read()),
            }
    except HTTPError as exc:
        return {
            "ok": False,
            "status_code": exc.code,
            "data": _decode_json(exc.read()),
            "reason": f"http_{exc.code}",
        }
    except URLError as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}


def _classify(raw: dict) -> str:
    """Map a seam response onto the house vocabulary, failing closed on unknown."""
    if raw.get("ok"):
        return STATUS_OK
    status_code = int(raw.get("status_code") or 0)
    if status_code == 429:
        return STATUS_RATE_LIMITED
    if status_code in {401, 403}:
        return STATUS_AUTH_FAILED
    if status_code == 0 or status_code >= 500:
        return STATUS_UNAVAILABLE
    return STATUS_UNKNOWN


class AmanahClient:
    """Route-name-only client. See the module docstring for the rules it enforces."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        cache: ResponseCache | None = None,
        limiter: ZoneLimiter | None = None,
    ) -> None:
        self._config = config
        self._cache = cache if cache is not None else ResponseCache()
        self._limiter = limiter if limiter is not None else ZoneLimiter()

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def _build(self, route: Route, path_params: dict | None, query: dict | None) -> tuple:
        path = route.path
        for key, value in (path_params or {}).items():
            placeholder = "{" + key + "}"
            if placeholder not in path:
                raise KeyError(f"route '{route.name}' has no path parameter '{key}'")
            path = path.replace(placeholder, str(value))
        if "{" in path:
            raise KeyError(f"route '{route.name}' is missing a path parameter: {path}")
        url = f"{self._config.base_url}{path}"
        if query:
            present = {k: v for k, v in query.items() if v is not None}
            if present:
                url = f"{url}?{urlencode(present)}"
        headers = {"Accept": "application/json", "User-Agent": self._config.user_agent}
        headers.update(_basic_header(self._config))
        headers.update(_operator_header_for(route, self._config))
        return url, headers

    def call(
        self,
        route_name: str,
        *,
        path_params: dict | None = None,
        query: dict | None = None,
        body: dict | None = None,
        bypass_cache: bool = False,
    ) -> dict:
        """Call one allowed route. Always returns a dict with a ``status`` key."""
        route = route_for(route_name)
        if body is not None and route.method != "POST":
            raise ValueError(f"route '{route.name}' is {route.method}; it takes no body")

        cacheable = route.method == "GET" and route.cache_ttl_seconds > 0 and not bypass_cache
        if not cacheable:
            return dict(self._perform(route, path_params, query, body), cached=False)

        key = cache_key(route.name, path_params, query)
        hit = self._cache.get(key)
        if hit is not None:
            return dict(hit, cached=True)

        is_leader, event = self._cache.begin_flight(key)
        if not is_leader:
            # A concurrent caller is already fetching this exact key. Wait for it rather
            # than spending a second PBKDF2 verification on the droplet for the same answer.
            event.wait(timeout=REQUEST_TIMEOUT_SECONDS)
            hit = self._cache.get(key)
            if hit is not None:
                return dict(hit, cached=True)
            return dict(self._perform(route, path_params, query, body), cached=False)

        try:
            result = self._perform(route, path_params, query, body)
            # Never cache a failure: a 5xx is a fact about the droplet at this moment,
            # not about the company being asked after (sec_edgar_cache.py's rule).
            if result["status"] == STATUS_OK:
                self._cache.put(key, result, ttl_seconds=route.cache_ttl_seconds)
            return dict(result, cached=False)
        finally:
            self._cache.end_flight(key)

    def _perform(self, route: Route, path_params, query, body) -> dict:
        url, headers = self._build(route, path_params, query)
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        timeout = REQUEST_TIMEOUT_SECONDS
        if route.name == "paper_execute":
            timeout = EXECUTE_TIMEOUT_SECONDS

        attempts = MAX_ATTEMPTS if route.retryable else 1
        raw: dict = {}
        for attempt in range(1, attempts + 1):
            if not self._limiter.acquire(route.zone):
                return {
                    "status": STATUS_RATE_LIMITED,
                    "route": route.name,
                    "status_code": 0,
                    "data": {},
                    "reason": "local_rate_limit_timeout",
                }
            raw = _request(route.method, url, headers, payload, timeout)
            if raw.get("ok"):
                break
            if attempt >= attempts:
                break
            if int(raw.get("status_code") or 0) not in RETRY_STATUS:
                break
            delay = min(BACKOFF_CAP_SECONDS, float(2 ** (attempt - 1)))
            time.sleep(delay * (1.0 + random.random() * 0.25))

        status = _classify(raw)
        result = {
            "status": status,
            "route": route.name,
            "status_code": raw.get("status_code", 0),
            "data": sanitize_json(raw.get("data") or {}),
        }
        if status != STATUS_OK:
            result["reason"] = raw.get("reason") or f"http_{raw.get('status_code')}"
        return result

    # --- Two entry points onto one endpoint, for one specific reason -------------------

    def read_compliance(self) -> dict:
        """Cached read, for answering a question about holdings."""
        return self.call("portfolio_compliance")

    def refresh_disposal_clock(self) -> dict:
        """Uncached read, scheduled daily.

        Calling GET /portfolio/compliance is what advances and PERSISTS the disposal
        clock -- holdings_compliance.apply_disposal_clock writes first_flagged_at, and
        nothing else in the system calls it. Serving this from cache would silently stop
        a religious deadline from advancing, so this path always goes to the droplet.
        """
        return self.call("portfolio_compliance", bypass_cache=True)
