"""The Telegram relay: the only process that can queue or execute anything.

## What it is, and what it deliberately is not

It is not a bot. No model runs here. The relay fetches its own facts, renders them with
`format.py`, composes with `compose.py`, and carries out exactly two human actions:

    tap 1  ->  POST /paper/approval        (queues; Basic auth only)
    tap 2  ->  POST /paper/execute/{id}    (submits; Basic auth + operator key)

nginx already splits the surface at exactly that line: `/paper/preview` and
`/paper/approval` need no operator key, while `/paper/(execute|reconcile)/` returns 401
without it. So the two taps are not two steps this code invented -- they are two
credential tiers the deployment already enforces.

## Buttons only

**A text message can never cause a write.** Text goes to the chat path; only an inline
button carries an action, and a button carries a server-side nonce rather than a symbol
or a queue id. There is no `/execute 12` command and there must never be one: a command
is a string a model or a forwarded message can produce, while a callback is a token this
process minted for one specific proposal.

Two identity checks, not one: the chat must be allowlisted **and** the presser must be the
owner's Telegram user id. Chat membership is not identity.

## The confirmation phrase

`EXECUTE PAPER` lives here as a module constant. It is in no tool schema, no role prompt
and no roster entry -- `test_bridge_no_secret_leak.py` asserts that -- so a model cannot
be talked into emitting the one string that matters, because it has never seen it in a
context where emitting it would do anything.

## Execute is opt-in twice over

Without `BRIDGE_OPERATOR_KEY` the relay still renders tap 2 and shows the exact payload it
would send, then stops. That is the Phase 3 default: the whole flow is exercisable before
anything can reach a broker.
"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bridge import proposals
from bridge.client import STATUS_OK, STATUS_UNAVAILABLE, STATUS_UNKNOWN
from bridge.format import render_blockers, render_shariah, render_shariah_us
from bridge.markets import detect_market

# The phrase POST /paper/execute requires. Deliberately only here.
EXECUTE_PHRASE = "EXECUTE PAPER"

TELEGRAM_API = "https://api.telegram.org"
POLL_TIMEOUT_SECONDS = 25
REQUEST_TIMEOUT_SECONDS = 35

ACTION_APPROVE = "a1"
ACTION_EXECUTE = "a2"
ACTION_REJECT = "no"

# Whether a market can execute is the backend's decision, read from /paper/status rather
# than restated here. An earlier version hardcoded "Malaysia cannot execute", which was
# true and was still a second copy of a rule that lives in broker_routing.py -- and a
# duplicated rule drifts the moment one copy changes.
MARKET_EXECUTION_NOTE = (
    "{market} execution is not available on this instance: {reason} "
    "The approval above is real and recorded."
)


def telegram_request(method: str, token: str, payload: dict, *, timeout=None) -> dict:
    """Single seam for every Telegram call. Never raises; same contract as the API client."""
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout or REQUEST_TIMEOUT_SECONDS) as response:
            return {"ok": True, "status_code": response.status, "data": json.loads(response.read())}
    except HTTPError as exc:
        return {"ok": False, "status_code": exc.code, "data": {}, "reason": f"http_{exc.code}"}
    except URLError as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}


class Relay:
    """Owns the operator key, the proposal store and the Telegram conversation."""

    def __init__(
        self,
        client,
        connection,
        *,
        token: str,
        owner_user_id: int,
        chat_id: int,
        topics: dict | None = None,
        transport=None,
    ) -> None:
        self._client = client
        self._db = connection
        self._token = token
        self._owner_user_id = int(owner_user_id)
        self._chat_id = int(chat_id)
        self._topics = topics or {}
        self._send = transport if transport is not None else telegram_request
        self._offset = 0

    # --- identity --------------------------------------------------------------------

    def is_authorised(self, update_callback: dict) -> bool:
        """Allowlisted chat AND the owner's user id. Membership alone is not identity."""
        sender = (update_callback.get("from") or {}).get("id")
        message = update_callback.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        return int(sender or 0) == self._owner_user_id and int(chat or 0) == self._chat_id

    @property
    def can_execute(self) -> bool:
        """True only if this process actually holds the operator key."""
        return bool(getattr(self._client, "_config", None) and self._client._config.operator_key)

    def market_execution(self, market: str) -> dict:
        """What the backend says about executing in this market. Fails closed.

        Read from /paper/status, which reports per-market routing, so the relay never
        has to restate a rule that belongs to broker_routing.py. An unreachable backend
        means "not enabled" -- a client that assumed yes would offer a button the gate
        chain would then refuse.
        """
        result = self._client.call("paper_status")
        if result["status"] != STATUS_OK:
            return {
                "enabled": False,
                "reason": f"execution status unavailable ({result.get('reason')}).",
            }
        markets = (result["data"] or {}).get("execution_markets") or {}
        entry = markets.get(str(market).upper())
        if entry is None:
            return {"enabled": False, "reason": f"the backend reports no routing for {market}."}
        reason = entry.get("reason") or "execution is not enabled for this market."
        return {
            "enabled": bool(entry.get("enabled")),
            "reason": reason,
            "adapter": entry.get("adapter"),
        }

    # --- outbound --------------------------------------------------------------------

    def send(self, text: str, *, buttons=None, topic: str | None = None) -> dict:
        payload = {"chat_id": self._chat_id, "text": text}
        thread = self._topics.get(topic) if topic else None
        if thread:
            payload["message_thread_id"] = thread
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self._send("sendMessage", self._token, payload)

    def edit(self, message_id: int, text: str, *, buttons=None) -> dict:
        payload = {"chat_id": self._chat_id, "message_id": message_id, "text": text}
        payload["reply_markup"] = {"inline_keyboard": buttons or []}
        return self._send("editMessageText", self._token, payload)

    def answer_callback(self, callback_id: str, text: str) -> dict:
        return self._send(
            "answerCallbackQuery",
            self._token,
            {"callback_query_id": callback_id, "text": text[:190]},
        )

    # --- proposals -------------------------------------------------------------------

    def propose(self, preview_body: dict) -> dict:
        """Run /paper/preview and, if it is ready, post a card with a tap-1 button."""
        result = self._client.call("paper_preview", body=preview_body)
        if result["status"] != STATUS_OK:
            return {"status": result["status"], "reason": result.get("reason")}

        payload = result["data"] or {}
        preview = dict(payload.get("preview") or {})
        preview["preview_id"] = payload.get("preview_id")
        symbol = str(preview.get("symbol") or preview_body.get("symbol") or "")
        market = detect_market(symbol)

        if preview.get("status") != "READY_FOR_APPROVAL":
            text = "\n\n".join(
                [
                    f"[Proposal refused] {symbol}",
                    render_blockers(preview.get("blocker_messages"), preview.get("blockers")),
                ]
            )
            self.send(text, topic="trades")
            return {"status": "REJECT", "reason": "preview_not_ready", "preview": preview}

        record = proposals.create_proposal(
            self._db, preview=preview, chat_id=self._chat_id, market=market
        )
        sent = self.send(
            self.render_card(record, preview),
            buttons=[
                [
                    {
                        "text": "Approve",
                        "callback_data": f"{ACTION_APPROVE}:{record['callback_nonce']}",
                    },
                    {
                        "text": "Reject",
                        "callback_data": f"{ACTION_REJECT}:{record['callback_nonce']}",
                    },
                ]
            ],
            topic="trades",
        )
        message_id = ((sent.get("data") or {}).get("result") or {}).get("message_id")
        if message_id:
            proposals.attach_message(self._db, record["proposal_id"], message_id=message_id)
        return {
            "status": "OK",
            "proposal": proposals.load_proposal(self._db, record["proposal_id"]),
        }

    def render_card(self, record: dict, preview: dict) -> str:
        """The card the owner reads before tapping. Every line built here, from backend fields.

        The whole oversight argument rests on this being informative. A card that said
        only "Buy 100 of 5225?" would still be a human click and would not be human
        oversight -- what makes the tap meaningful is that the verdict, its source
        document, the signal's provenance and the risk status are all visible at the
        moment of deciding.
        """
        agents = preview.get("agent_summary") or {}
        shariah = agents.get("shariah") or {}
        quant = agents.get("quant") or {}
        risk = agents.get("risk") or {}

        lines = [
            f"[Proposal] {record['symbol']} {record['side']} x{record['quantity']} "
            f"@ {record['price']} ({record['asset_class']}, {record['market']})",
            "",
        ]
        if shariah.get("market") == "US" and shariah.get("provider") == "SEC_EDGAR":
            lines.append(render_shariah_us({"symbol": record["symbol"], **shariah}))
        else:
            lines.append(
                render_shariah(
                    {"ticker": record["symbol"], "verdict": shariah.get("details") or shariah}
                )
            )
        lines.append("")
        lines.append(
            f"[Quant]  {quant.get('signal')} ({quant.get('reason')}) "
            f"from {quant.get('price_source')}, {quant.get('bars')} bars"
        )
        lines.append(f"[Risk]   {risk.get('status')} ({risk.get('reason')})")
        lines.append(f"[Notional] {preview.get('notional')}")
        lines.append("")
        lines.append(
            f"Expires in {int(record['expires_at'] - record['created_at'])}s -- after that a "
            "re-quote is required, because the price it rests on will have moved."
        )
        return "\n".join(lines)

    # --- tap 1 -----------------------------------------------------------------------

    def approve(self, record: dict) -> dict:
        """Tap 1: queue the approval. Basic auth only -- no operator key involved."""
        if record["status"] != proposals.STATUS_PENDING:
            return {"status": "REJECT", "reason": f"not_pending_{record['status'].lower()}"}

        result = self._client.call(
            "paper_approval", body={"preview": record["preview"], "approved": True}
        )
        if result["status"] != STATUS_OK:
            return {"status": result["status"], "reason": result.get("reason")}

        payload = result["data"] or {}
        approval = payload.get("approval") or {}
        if approval.get("status") != "APPROVED_PAPER_READY":
            proposals.mark_rejected(
                self._db,
                record["proposal_id"],
                reason=str(approval.get("reason") or approval.get("status") or "not_approved"),
                approval=payload,
            )
            return {"status": "REJECT", "reason": approval.get("reason"), "approval": payload}

        queued = proposals.mark_queued(self._db, record["proposal_id"], approval=payload)
        if queued["status"] != "OK":
            return queued
        return {"status": "OK", "proposal": queued["proposal"], "approval": payload}

    def render_queued(self, record: dict, approval_payload: dict) -> str:
        """Tap 1's reply: the queue id and the exact body tap 2 will send. Nothing invented."""
        approval = approval_payload.get("approval") or {}
        trace = approval.get("shariah_trace") or ""
        body = {"confirmation_phrase": EXECUTE_PHRASE}
        lines = [
            f"Queued as #{approval_payload.get('queue_id')}.",
            "",
            f"  {record['symbol']} {record['side']} x{record['quantity']} @ {record['price']}",
            f"  Status        {approval.get('status')}",
        ]
        if trace:
            lines.append(f"  Trace         {trace}")
        lines.append("")
        capability = self.market_execution(record["market"])
        if not capability["enabled"]:
            lines.append(
                MARKET_EXECUTION_NOTE.format(market=record["market"], reason=capability["reason"])
            )
            return "\n".join(lines)
        lines.append(f"Tap 2 will POST /paper/execute/{approval_payload.get('queue_id')} with:")
        lines.append(f"  {json.dumps(body)}")
        if not self.can_execute:
            lines.append("")
            lines.append(
                "This relay holds no operator key, so tap 2 stops at a dry run and "
                "nothing reaches the broker."
            )
        return "\n".join(lines)

    def execute_buttons(self, record: dict) -> list:
        """No execute button for a market the backend will not execute. Asked, not assumed."""
        if not self.market_execution(record["market"])["enabled"]:
            return []
        return [
            [
                {
                    "text": "Confirm EXECUTE",
                    "callback_data": f"{ACTION_EXECUTE}:{record['callback_nonce']}",
                },
                {"text": "Cancel", "callback_data": f"{ACTION_REJECT}:{record['callback_nonce']}"},
            ]
        ]

    # --- tap 2 -----------------------------------------------------------------------

    def execute(self, record: dict) -> dict:
        """Tap 2: submit. Claims the right to do so first, so a double tap cannot double-send."""
        capability = self.market_execution(record["market"])
        if not capability["enabled"]:
            # Not offering a button is not the same as refusing the action -- a replayed
            # callback must still be stopped here.
            return {
                "status": "REJECT",
                "reason": "market_execution_unavailable",
                "market": record["market"],
                "detail": capability["reason"],
            }

        claim = proposals.claim_execution(self._db, record["proposal_id"])
        if claim["status"] != "OK":
            return claim

        if not self.can_execute:
            dry = {
                "dry_run": True,
                "queue_id": record["queue_id"],
                "would_send": {"confirmation_phrase": EXECUTE_PHRASE},
            }
            proposals.record_execution(self._db, record["proposal_id"], result=dry, dry_run=True)
            return {"status": "DRY_RUN", "result": dry}

        result = self._client.call(
            "paper_execute",
            path_params={"queue_id": record["queue_id"]},
            body={"confirmation_phrase": EXECUTE_PHRASE},
        )
        # A timeout is not a failure to retry. The request may or may not have reached the
        # broker, and resending to find out is how you get two orders.
        uncertain = result["status"] in {STATUS_UNKNOWN, STATUS_UNAVAILABLE}
        proposals.record_execution(
            self._db, record["proposal_id"], result=result, uncertain=uncertain
        )
        if uncertain:
            return {"status": "EXECUTION_UNCERTAIN", "result": result}
        return {"status": result["status"], "result": result}

    # --- callbacks -------------------------------------------------------------------

    def handle_callback(self, callback: dict) -> dict:
        """One inline-button press. Refuses anything it did not mint for this owner."""
        if not self.is_authorised(callback):
            self.answer_callback(callback.get("id", ""), "Not authorised.")
            return {"status": "REJECT", "reason": "unauthorised"}

        data = str(callback.get("data") or "")
        action, _, nonce = data.partition(":")
        record = proposals.resolve_nonce(self._db, nonce) if nonce else None
        if record is None:
            self.answer_callback(callback.get("id", ""), "That proposal is no longer known.")
            return {"status": "REJECT", "reason": "unknown_nonce"}

        proposals.expire_due(self._db)
        record = proposals.load_proposal(self._db, record["proposal_id"])

        if action == ACTION_REJECT:
            proposals.hold_proposal(self._db, record["proposal_id"], reason="rejected_by_owner")
            self.answer_callback(callback.get("id", ""), "Rejected.")
            self.edit(record["message_id"] or 0, "Rejected by owner. Nothing was submitted.")
            return {"status": "OK", "action": "reject"}

        if action == ACTION_APPROVE:
            if record["status"] != proposals.STATUS_PENDING:
                return self._refuse_stale(callback, record)
            outcome = self.approve(record)
            if outcome["status"] != "OK":
                self.answer_callback(callback.get("id", ""), "Not approved.")
                self.edit(
                    record["message_id"] or 0,
                    f"Not approved: {outcome.get('reason')}. Nothing was queued.",
                )
                return outcome
            updated = outcome["proposal"]
            self.answer_callback(callback.get("id", ""), "Queued.")
            self.edit(
                record["message_id"] or 0,
                self.render_queued(updated, outcome["approval"]),
                buttons=self.execute_buttons(updated),
            )
            return {"status": "OK", "action": "approve", "proposal": updated}

        if action == ACTION_EXECUTE:
            if record["status"] != proposals.STATUS_QUEUED:
                return self._refuse_stale(callback, record)
            outcome = self.execute(record)
            self.answer_callback(callback.get("id", ""), outcome["status"])
            self.edit(record["message_id"] or 0, self._render_execution(record, outcome))
            return {"status": "OK", "action": "execute", "outcome": outcome}

        self.answer_callback(callback.get("id", ""), "Unknown action.")
        return {"status": "REJECT", "reason": "unknown_action"}

    def _refuse_stale(self, callback: dict, record: dict) -> dict:
        """An expired or already-acted proposal offers a re-quote, never a resend."""
        self.answer_callback(callback.get("id", ""), f"No longer actionable ({record['status']}).")
        self.edit(
            record["message_id"] or 0,
            f"This proposal is {record['status']} ({record.get('status_reason') or 'already acted on'}).\n"
            "Re-quote to act: the price it rested on has moved.",
        )
        return {"status": "REJECT", "reason": f"stale_{record['status'].lower()}"}

    def _render_execution(self, record: dict, outcome: dict) -> str:
        if outcome["status"] == "DRY_RUN":
            return (
                f"Dry run for queue #{record['queue_id']}. This relay holds no operator "
                f"key, so nothing was submitted.\nWould have sent: "
                f"{json.dumps(outcome['result']['would_send'])}"
            )
        if outcome["status"] == "ALREADY_CLAIMED":
            return f"Already acted on ({outcome.get('reason')}). Nothing was sent twice."
        if outcome["status"] == "EXECUTION_UNCERTAIN":
            return (
                f"Queue #{record['queue_id']}: the outcome is UNKNOWN. The request may or "
                "may not have reached the broker, and it will not be retried.\n"
                "Check /paper/status and reconcile before acting again."
            )
        data = (outcome.get("result") or {}).get("data") or {}
        return f"Queue #{record['queue_id']}: {data.get('status') or outcome['status']}."

    # --- the loop --------------------------------------------------------------------

    def poll_once(self, *, timeout=None) -> list:
        """One getUpdates pass. Text is never an action; only callbacks are."""
        response = self._send(
            "getUpdates",
            self._token,
            {
                "offset": self._offset,
                "timeout": timeout if timeout is not None else POLL_TIMEOUT_SECONDS,
                "allowed_updates": ["callback_query", "message"],
            },
        )
        handled = []
        for update in (response.get("data") or {}).get("result") or []:
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            callback = update.get("callback_query")
            if callback:
                handled.append(self.handle_callback(callback))
            # A message is deliberately ignored here. Conversation belongs to OpenClaw;
            # this process exists to carry out two button presses and nothing else.
        return handled

    def sweep(self, *, now=None) -> list:
        """Expire anything stale and tell the owner, so a dead card is never mistaken for live."""
        moved = proposals.expire_due(self._db, now=now)
        for proposal_id in moved:
            record = proposals.load_proposal(self._db, proposal_id)
            if record and record.get("message_id"):
                self.edit(
                    record["message_id"],
                    f"Expired ({record.get('status_reason')}). Re-quote to act; the price "
                    "this rested on has moved.",
                )
        return moved

    def run(self, *, iterations=None, sleep_seconds=1.0) -> None:  # pragma: no cover - loop
        count = 0
        while iterations is None or count < iterations:
            self.sweep()
            self.poll_once()
            count += 1
            if iterations is None:
                time.sleep(sleep_seconds)


def build_callback_data(action: str, nonce: str) -> str:
    """Telegram caps callback_data at 64 bytes; this must stay comfortably inside."""
    data = f"{action}:{nonce}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("callback_data exceeds Telegram's 64-byte limit")
    return data
