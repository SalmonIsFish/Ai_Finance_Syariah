"""Polling jobs. Nothing on the droplet can call out, so the laptop has to ask.

`VPS_RUNBOOK.md:387` lists "no monitoring or alerting" as a known gap, and it is literal:
there is no scheduler, no webhook, no mail and no queue on the deployed instance -- only
two loopback cron jobs that warm caches. A holding can go overdue and nothing tells
anyone. So every alert in this system is this file asking.

## Two rules, both enforced by test_bridge_route_allowlist.py

* **No scheduled job may call `/paper/preview`.** It sits on a live SEC fetch (0.7-2s
  cold) and it is an order-shaped action that writes an evidence record with
  `source: "preview"`. A polling loop would flood `/api/evidence` with records
  indistinguishable from orders the owner actually considered, and that distinction
  cannot be recovered afterwards.
* **No scheduled job may call `/news` or `/copilot/*`.** Uncapped OpenRouter spend.

## The disposal clock is the one job that must never be served from cache

Calling `GET /portfolio/compliance` is what *advances and persists* the disposal clock --
`holdings_compliance.apply_disposal_clock` writes `first_flagged_at`, and nothing else in
the system calls it. A cached response would mean the clock silently stops. So this job
uses `client.refresh_disposal_clock()`, which bypasses the cache, and it is the only
caller that does.

## Gentle on purpose

Every request costs a 260,000-iteration PBKDF2 verification on a 2 GB droplet, because
there is no session or token. Steady state here is roughly 0.3 requests per minute against
a 240 r/m allowance -- two orders of magnitude under, so an owner asking questions has the
whole budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

MINUTE = 60.0
HOUR = 60.0 * MINUTE


@dataclass
class Job:
    """One recurring check. `interval_seconds` is a floor, not a promise."""

    name: str
    interval_seconds: float
    run: Callable
    topic: str = "general"
    next_run_at: float = 0.0
    last_error: str | None = None

    def due(self, now: float) -> bool:
        return now >= self.next_run_at

    def schedule_next(self, now: float) -> None:
        self.next_run_at = now + self.interval_seconds


@dataclass
class Scheduler:
    """A plain loop with per-job timestamps.

    No APScheduler: `backend/requirements.txt` is fastapi, uvicorn and yfinance, and a
    dependency whose whole job is `now >= next_run_at` is not worth adding.
    """

    jobs: list = field(default_factory=list)

    def add(self, job: Job) -> None:
        self.jobs.append(job)

    def due(self, now: float | None = None) -> list:
        moment = time.time() if now is None else now
        return [job for job in self.jobs if job.due(moment)]

    def tick(self, now: float | None = None) -> list:
        """Run whatever is due. A failing job never stops the others."""
        moment = time.time() if now is None else now
        results = []
        for job in self.due(moment):
            try:
                outcome = job.run()
                job.last_error = None
            except Exception as exc:  # a broken job must not take the relay down
                outcome = {"status": "ERROR", "reason": type(exc).__name__}
                job.last_error = type(exc).__name__
            job.schedule_next(moment)
            results.append({"job": job.name, "result": outcome})
        return results


# --- the jobs ------------------------------------------------------------------------


def disposal_clock_job(client, notify) -> dict:
    """Advance the disposal clock and report anything overdue or close to it.

    This call IS the clock. See the module docstring.
    """
    result = client.refresh_disposal_clock()
    if result["status"] != "OK":
        return {"status": result["status"], "reason": result.get("reason")}

    from bridge.format import render_compliance

    facts = result["data"] or {}
    screening = facts.get("screening") or {}
    flagged = screening.get("flagged") or []
    overdue = int(screening.get("overdue_count") or 0)

    # `overdue` is only present on NON_COMPLIANT_HOLDING entries, so read it with .get().
    # `days_remaining` is floored and goes negative once a deadline has passed, and it is
    # None on an unconfirmed holding -- which is not "safe", it is "needs attention".
    urgent = [
        holding
        for holding in flagged
        if holding.get("alert") == "NON_COMPLIANT_HOLDING"
        and (
            holding.get("overdue")
            or holding.get("days_remaining") is None
            or int(holding.get("days_remaining", 99)) <= 7
        )
    ]
    if overdue or urgent:
        notify(render_compliance(facts), topic="compliance")
    return {"status": "OK", "overdue": overdue, "urgent": len(urgent)}


def publication_watch_job(client, notify, seen: set) -> dict:
    """Alert when a new SC publication appears or a different one becomes active.

    This is the single event that can flip a holding from compliant to not, and nothing
    in the system announces it today.
    """
    result = client.call("publications")
    if result["status"] != "OK":
        return {"status": result["status"], "reason": result.get("reason")}

    from bridge.format import render_publications

    publications = (result["data"] or {}).get("publications") or []
    # Keyed on the document hash and the activation as well as the id: a re-ingested
    # publication with the same id but a different document is a different fact.
    fingerprints = {
        (p.get("id"), p.get("source_document_hash"), p.get("activated_at")) for p in publications
    }
    fresh = fingerprints - seen
    if seen and fresh:
        notify(render_publications(result["data"]), topic="publications")
    seen.clear()
    seen.update(fingerprints)
    return {"status": "OK", "new": len(fresh)}


def risk_watch_job(client, notify) -> dict:
    """Alert when a risk number goes unbounded -- the backend's way of saying stop."""
    result = client.call("risk_snapshot")
    if result["status"] != "OK":
        return {"status": result["status"], "reason": result.get("reason")}

    from bridge.format import render_risk
    from bridge.sanitize import non_finite_label

    facts = result["data"] or {}
    blocking = [
        name
        for name in ("daily_loss_pct", "weekly_loss_pct")
        if non_finite_label(facts.get(name)) is not None
    ]
    if blocking:
        notify(render_risk(facts), topic="risk")
    return {"status": "OK", "blocking": blocking}


def build_default_schedule(client, notify) -> Scheduler:
    """The standing jobs. Intervals chosen for what changes, not for what is pollable."""
    seen_publications: set = set()
    scheduler = Scheduler()
    # A 30-day window does not need checking more than daily, and this is the heaviest
    # endpoint in the system -- it screens every holding, and US holdings go through a
    # live SEC fetch of up to ~4.7 MB each.
    scheduler.add(
        Job("disposal_clock", 24 * HOUR, lambda: disposal_clock_job(client, notify), "compliance")
    )
    scheduler.add(
        Job(
            "publication_watch",
            30 * MINUTE,
            lambda: publication_watch_job(client, notify, seen_publications),
            "publications",
        )
    )
    scheduler.add(Job("risk_watch", 5 * MINUTE, lambda: risk_watch_job(client, notify), "risk"))
    return scheduler
