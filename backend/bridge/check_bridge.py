"""Smoke-check the bridge against the deployed API. Prints booleans, never values.

Mirrors backend/check_config.py: it says whether a credential is configured, never what
it is. Run it after setting backend/bridge/.env to confirm the spine works end to end
before any bot exists.

    .\\.venv\\Scripts\\python.exe backend\\bridge\\check_bridge.py --symbol 4197
    .\\.venv\\Scripts\\python.exe backend\\bridge\\check_bridge.py --symbol AAPL
    .\\.venv\\Scripts\\python.exe backend\\bridge\\check_bridge.py --risk

Malaysian symbols (numeric Bursa codes) are the interesting case: they exercise the SC
list path and the Yahoo price source, neither of which the US path touches.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BRIDGE_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from bridge.client import AmanahClient, load_bridge_config  # noqa: E402
from bridge.format import render_compliance, render_quant, render_risk, render_shariah  # noqa: E402


def _load_env_file(path: Path) -> None:
    """Read backend/bridge/.env the way config.py reads backend/.env.

    setdefault, so the first occurrence of a key wins and a real environment variable
    always beats the file -- the same rule (and the same gotcha) as config.py:29.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _print_config(config) -> None:
    print("Bridge configuration")
    print(f"  base url:                {config.base_url}")
    print(f"  basic user configured:   {bool(config.basic_user)}")
    print(f"  basic password set:      {bool(config.basic_password)}")
    print(f"  operator key present:    {bool(config.operator_key)}")
    print("")


def _report(label: str, result: dict) -> dict:
    status = result.get("status")
    cached = result.get("cached")
    print(f"{label}: {status} (http {result.get('status_code')}, cached={cached})")
    if status != "OK":
        print(f"  reason: {result.get('reason')}")
    return result.get("data") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="ticker to screen, e.g. 4197 or AAPL")
    parser.add_argument("--risk", action="store_true", help="fetch the risk snapshot")
    parser.add_argument("--compliance", action="store_true", help="fetch holdings compliance")
    parser.add_argument(
        "--operator",
        action="store_true",
        help="load the operator key too (relay mode); it is still never printed",
    )
    args = parser.parse_args()

    _load_env_file(BRIDGE_DIR / ".env")

    try:
        config = load_bridge_config(with_operator=args.operator)
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        return 1

    _print_config(config)
    client = AmanahClient(config)

    if args.symbol:
        data = _report(
            "shariah_status", client.call("shariah_status", path_params={"ticker": args.symbol})
        )
        if data:
            print(render_shariah(data))
        print("")
        data = _report(
            "quant_signal", client.call("quant_signal", path_params={"ticker": args.symbol})
        )
        if data:
            print(render_quant(data))
        print("")

    if args.risk:
        data = _report("risk_snapshot", client.call("risk_snapshot"))
        if data:
            print(render_risk(data))
        print("")

    if args.compliance:
        # Deliberately the cached read. The uncached refresh_disposal_clock() advances
        # and persists the clock, which is a scheduled job's business, not a smoke test's.
        data = _report("portfolio_compliance", client.read_compliance())
        if data:
            print(render_compliance(data))
        print("")

    if not (args.symbol or args.risk or args.compliance):
        print("Nothing requested. Pass --symbol, --risk or --compliance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
