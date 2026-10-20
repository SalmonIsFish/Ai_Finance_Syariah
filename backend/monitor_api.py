"""HTTP surface for the Shariah monitor (holdings_monitor). Owner-only.

Built by a factory rather than importing local_api, which would be circular:
local_api passes in its own connection factory, owner dependency and price
lookup, so this router uses exactly the same database, auth and prices.

Every write is a POST: the API's CORS policy allows GET and POST only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import holdings_monitor
from holdings_monitor import HoldingError


class HoldingIn(BaseModel):
    symbol: str
    quantity: float | str
    average_cost: float | str
    broker: str = "manual"


class HoldingRef(BaseModel):
    symbol: str
    broker: str = "manual"


class CsvImportIn(BaseModel):
    broker: str
    csv: str


class SaleIn(BaseModel):
    symbol: str
    quantity: float | str
    price: float | str
    broker: str = "manual"
    sold_at: str | None = None


class SettingsIn(BaseModel):
    methodology: str


class EscalationIn(BaseModel):
    symbol: str
    detail: str
    report_date: str | None = None


def build_router(connection_factory, owner_dependency, price_lookup, filing_reviewer=None):
    router = APIRouter(prefix="/monitor", dependencies=[Depends(owner_dependency)])

    def _with_connection(fn):
        connection = connection_factory()
        try:
            return fn(connection)
        except HoldingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        finally:
            connection.close()

    @router.get("")
    def run():
        """Screen and colour every holding. Writes: it advances the disposal clock
        and records colour changes, so the bridge calls it uncached."""
        return _with_connection(
            lambda c: holdings_monitor.run_monitor(c, price_lookup=price_lookup)
        )

    @router.get("/holdings")
    def holdings():
        return {"holdings": _with_connection(holdings_monitor.list_holdings)}

    @router.post("/holdings")
    def add_holding(body: HoldingIn):
        return _with_connection(lambda c: holdings_monitor.upsert_holding(c, **body.model_dump()))

    @router.post("/holdings/remove")
    def remove_holding(body: HoldingRef):
        return _with_connection(lambda c: holdings_monitor.remove_holding(c, **body.model_dump()))

    @router.post("/holdings/import")
    def import_csv(body: CsvImportIn):
        result = _with_connection(
            lambda c: holdings_monitor.import_holdings_csv(c, body.csv, broker=body.broker)
        )
        if result["status"] != "OK":
            raise HTTPException(status_code=400, detail=result)
        return result

    @router.get("/sales")
    def sales():
        return {"sales": _with_connection(holdings_monitor.list_sales)}

    @router.post("/sales")
    def record_sale(body: SaleIn):
        return _with_connection(lambda c: holdings_monitor.record_sale(c, **body.model_dump()))

    @router.post("/escalate")
    def escalate(body: EscalationIn):
        """Mark a holding RED for one annual report. There is no endpoint that
        marks one GREEN: a YELLOW or RED clears only when a newer report does."""
        return _with_connection(
            lambda c: holdings_monitor.add_review(
                c,
                symbol=body.symbol,
                kind="escalation",
                detail=body.detail,
                report_date=body.report_date,
                source="owner",
            )
        )

    @router.get("/settings")
    def settings():
        return {"methodology": _with_connection(holdings_monitor.get_methodology)}

    @router.post("/settings")
    def update_settings(body: SettingsIn):
        return _with_connection(lambda c: holdings_monitor.set_methodology(c, body.methodology))

    @router.get("/alerts")
    def alerts(limit: int = 50):
        return {"alerts": _with_connection(lambda c: holdings_monitor.list_alerts(c, limit=limit))}

    @router.get("/reviews/{symbol}")
    def reviews(symbol: str, report_date: str | None = None):
        return {
            "reviews": _with_connection(
                lambda c: holdings_monitor.reviews_for(
                    c, holdings_monitor.normalize_symbol(symbol), report_date
                )
            )
        }

    if filing_reviewer is not None:

        @router.post("/review/{symbol}")
        def review_filing(symbol: str):
            """Have a model read the latest annual report. It can only add a
            YELLOW flag (or a note); it never decides a colour."""
            return _with_connection(lambda c: filing_reviewer(c, symbol))

    return router
