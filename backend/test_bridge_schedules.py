"""The polling jobs: gentle, cache-correct, and never order-shaped.

Nothing on the droplet can call out -- `VPS_RUNBOOK.md:387` lists "no monitoring or
alerting" as a known gap -- so every alert this system produces is the laptop asking. That
makes the polling design load-bearing in two directions at once: too quiet and a disposal
deadline passes unnoticed; too loud and a 2 GB droplet spends its CPU on PBKDF2.
"""

import pytest
from bridge import schedules
from bridge.sanitize import sanitize_json

COMPLIANCE_CLEAN = {
    "screening": {
        "status": "OK",
        "position_count": 2,
        "flagged": [],
        "flagged_count": 0,
        "non_compliant_count": 0,
        "unconfirmed_count": 0,
        "disposal_window_days": 30,
        "overdue_count": 0,
    },
    "purification": {},
}


def flagged(**overrides):
    holding = {
        "symbol": "0026",
        "alert": "NON_COMPLIANT_HOLDING",
        "disposal_deadline": "2026-06-28",
        "days_remaining": 20,
        "deadline_basis": "publication_date",
        "overdue": False,
    }
    holding.update(overrides)
    return {
        "screening": {
            "status": "OK",
            "position_count": 1,
            "flagged": [holding],
            "flagged_count": 1,
            "non_compliant_count": 1,
            "unconfirmed_count": 0,
            "disposal_window_days": 30,
            "overdue_count": 1 if holding.get("overdue") else 0,
        },
        "purification": {},
    }


class FakeClient:
    def __init__(self, data=None, status="OK"):
        self.calls = []
        self.refreshes = 0
        self._data = data if data is not None else {}
        self._status = status

    def call(self, route_name, **kwargs):
        self.calls.append(route_name)
        return {"status": self._status, "route": route_name, "data": self._data}

    def read_compliance(self):
        raise AssertionError("the scheduled job must not use the cached read")

    def refresh_disposal_clock(self):
        self.refreshes += 1
        self.calls.append("portfolio_compliance")
        return {"status": self._status, "route": "portfolio_compliance", "data": self._data}

    def refresh_monitor(self):
        self.refreshes += 1
        self.calls.append("monitor_run")
        return {"status": self._status, "route": "monitor_run", "data": self._data}


def collector():
    sent = []

    def notify(text, *, topic="general"):
        sent.append({"text": text, "topic": topic})

    return notify, sent


# --- the scheduler ------------------------------------------------------------------


def test_a_job_runs_when_due_and_not_before():
    runs = []
    scheduler = schedules.Scheduler()
    scheduler.add(schedules.Job("j", 60.0, lambda: runs.append(1) or {"status": "OK"}))

    scheduler.tick(now=0.0)
    assert len(runs) == 1
    scheduler.tick(now=59.0)
    assert len(runs) == 1, "a job must not run before its interval has elapsed"
    scheduler.tick(now=60.0)
    assert len(runs) == 2


def test_a_failing_job_never_stops_the_others():
    """A broken alert must not take down the relay that carries the approval buttons."""
    ran = []

    def boom():
        raise ValueError("kaboom")

    scheduler = schedules.Scheduler()
    scheduler.add(schedules.Job("bad", 1.0, boom))
    scheduler.add(schedules.Job("good", 1.0, lambda: ran.append(1) or {"status": "OK"}))

    results = scheduler.tick(now=0.0)

    assert ran == [1]
    assert results[0]["result"]["status"] == "ERROR"
    assert scheduler.jobs[0].last_error == "ValueError"


def test_a_failing_job_is_rescheduled_rather_than_retried_immediately():
    scheduler = schedules.Scheduler()
    scheduler.add(schedules.Job("bad", 300.0, lambda: (_ for _ in ()).throw(ValueError())))
    scheduler.tick(now=0.0)
    assert scheduler.due(now=10.0) == []


# --- the disposal clock -------------------------------------------------------------


def test_the_disposal_job_uses_the_uncached_path():
    """Calling /portfolio/compliance IS the clock. A cache hit silently stops it."""
    client = FakeClient(COMPLIANCE_CLEAN)
    notify, _ = collector()

    schedules.disposal_clock_job(client, notify)

    assert client.refreshes == 1


def test_a_clean_book_produces_no_alert():
    client = FakeClient(COMPLIANCE_CLEAN)
    notify, sent = collector()
    result = schedules.disposal_clock_job(client, notify)
    assert result["overdue"] == 0
    assert sent == [], "a quiet day must be quiet, or the owner stops reading the alerts"


@pytest.mark.parametrize(
    "holding",
    [
        {"overdue": True, "days_remaining": -87},
        {"days_remaining": 3},
        # None on a NON_COMPLIANT holding means neither date parsed. That needs
        # attention; it does not mean there is time.
        {"days_remaining": None},
    ],
)
def test_an_urgent_holding_is_alerted(holding):
    client = FakeClient(flagged(**holding))
    notify, sent = collector()

    schedules.disposal_clock_job(client, notify)

    assert len(sent) == 1
    assert sent[0]["topic"] == "compliance"
    assert "0026" in sent[0]["text"]


def test_a_distant_deadline_is_not_alerted_daily():
    client = FakeClient(flagged(days_remaining=25))
    notify, sent = collector()
    schedules.disposal_clock_job(client, notify)
    assert sent == []


def test_an_unavailable_backend_is_reported_not_treated_as_clean():
    """A failed fetch must never read as 'nothing is overdue'."""
    client = FakeClient({}, status="UNAVAILABLE")
    notify, sent = collector()

    result = schedules.disposal_clock_job(client, notify)

    assert result["status"] == "UNAVAILABLE"
    assert "overdue" not in result


# --- the publication watch ----------------------------------------------------------


def _publications(*fingerprints):
    return {
        "publications": [
            {
                "id": i,
                "source_document_hash": h,
                "activated_at": a,
                "publication_date": "2026-05-29",
            }
            for i, h, a in fingerprints
        ]
    }


def test_the_first_run_records_a_baseline_without_alerting():
    """Otherwise every restart announces the entire history as new."""
    client = FakeClient(_publications(("p1", "aaa", None)))
    notify, sent = collector()
    seen = set()

    schedules.publication_watch_job(client, notify, seen)

    assert sent == []
    assert len(seen) == 1


def test_a_new_publication_alerts():
    notify, sent = collector()
    seen = set()
    schedules.publication_watch_job(FakeClient(_publications(("p1", "aaa", None))), notify, seen)
    schedules.publication_watch_job(
        FakeClient(_publications(("p1", "aaa", None), ("p2", "bbb", None))), notify, seen
    )
    assert len(sent) == 1
    assert sent[0]["topic"] == "publications"


def test_an_activation_alerts_even_though_the_id_is_unchanged():
    """Activation is the event that can flip a holding, not ingestion."""
    notify, sent = collector()
    seen = set()
    schedules.publication_watch_job(FakeClient(_publications(("p1", "aaa", None))), notify, seen)
    schedules.publication_watch_job(
        FakeClient(_publications(("p1", "aaa", "2026-09-22T10:00:00Z"))), notify, seen
    )
    assert len(sent) == 1


def test_a_reingested_document_under_the_same_id_alerts():
    """Same id, different document, is a different fact."""
    notify, sent = collector()
    seen = set()
    schedules.publication_watch_job(FakeClient(_publications(("p1", "aaa", None))), notify, seen)
    schedules.publication_watch_job(FakeClient(_publications(("p1", "zzz", None))), notify, seen)
    assert len(sent) == 1


def test_an_unchanged_list_is_silent():
    notify, sent = collector()
    seen = set()
    payload = _publications(("p1", "aaa", "2026-09-22T10:00:00Z"))
    schedules.publication_watch_job(FakeClient(payload), notify, seen)
    schedules.publication_watch_job(FakeClient(payload), notify, seen)
    assert sent == []


# --- the risk watch ------------------------------------------------------------------


def test_an_unbounded_loss_alerts():
    import math

    client = FakeClient(sanitize_json({"orders_today": 1, "daily_loss_pct": math.inf}))
    notify, sent = collector()

    result = schedules.risk_watch_job(client, notify)

    assert result["blocking"] == ["daily_loss_pct"]
    assert "UNBOUNDED" in sent[0]["text"]


def test_a_finite_risk_snapshot_is_silent():
    client = FakeClient({"orders_today": 1, "daily_loss_pct": 0.4, "weekly_loss_pct": 0.9})
    notify, sent = collector()
    assert schedules.risk_watch_job(client, notify)["blocking"] == []
    assert sent == []


# --- the standing schedule -----------------------------------------------------------


def test_the_default_schedule_is_gentle_on_the_droplet():
    """Every request costs a 260,000-iteration PBKDF2 on a 2 GB box."""
    notify, _ = collector()
    scheduler = schedules.build_default_schedule(FakeClient(), notify)

    per_hour = sum(3600.0 / job.interval_seconds for job in scheduler.jobs)

    assert per_hour < 20, f"{per_hour:.1f} requests/hour is not a background load"
    assert all(job.interval_seconds >= 5 * schedules.MINUTE for job in scheduler.jobs)


def test_the_disposal_clock_runs_daily_not_hourly():
    """It screens every holding, and US holdings each cost a live multi-MB SEC fetch."""
    notify, _ = collector()
    scheduler = schedules.build_default_schedule(FakeClient(), notify)
    job = next(j for j in scheduler.jobs if j.name == "disposal_clock")
    assert job.interval_seconds >= 12 * schedules.HOUR


# --- the Shariah monitor ------------------------------------------------------------

MONITOR_CHANGED = {
    "status": "OK",
    "counts": {"GREEN": 3, "YELLOW": 1, "RED": 1},
    "transitions": [
        {
            "symbol": "NVDA",
            "from": "GREEN",
            "to": "YELLOW",
            "message": "NVDA: GREEN -> YELLOW. Under review. Do not buy more or sell.",
        }
    ],
    "basis": "Applies the SC Malaysia SAC screening methodology. Not a Shariah ruling.",
}


def test_a_colour_change_is_relayed_with_the_backend_message():
    client = FakeClient(MONITOR_CHANGED)
    notify, sent = collector()

    result = schedules.monitor_job(client, notify)

    assert result == {"status": "OK", "changes": 1}
    assert client.refreshes == 1 and client.calls == ["monitor_run"]
    [message] = sent
    assert message["topic"] == "compliance"
    assert "NVDA: GREEN -> YELLOW (under review)" in message["text"]
    assert "Do not buy more or sell" in message["text"]
    assert "Not a Shariah ruling" in message["text"]


def test_no_change_is_silent():
    client = FakeClient({**MONITOR_CHANGED, "transitions": []})
    notify, sent = collector()
    assert schedules.monitor_job(client, notify) == {"status": "OK", "changes": 0}
    assert sent == []


def test_an_unavailable_monitor_is_reported_not_treated_as_quiet():
    notify, sent = collector()
    result = schedules.monitor_job(FakeClient(status="UNAVAILABLE"), notify)
    assert result["status"] == "UNAVAILABLE"
    assert sent == []


def test_the_monitor_runs_daily_and_uncached():
    notify, _ = collector()
    scheduler = schedules.build_default_schedule(FakeClient(), notify)
    job = next(j for j in scheduler.jobs if j.name == "monitor")
    assert job.interval_seconds >= 12 * schedules.HOUR
    from bridge.routes import ROUTES

    assert ROUTES["monitor_run"].cache_ttl_seconds == 0.0
