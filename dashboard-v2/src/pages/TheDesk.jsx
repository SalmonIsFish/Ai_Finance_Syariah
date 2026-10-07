import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import OfficerCard from "../components/OfficerCard";
import EvidenceTrail from "../components/EvidenceTrail";
import ErrorNote from "../components/ErrorNote";
import { fetchPreview, submitApproval, fetchRiskSnapshot, fetchPaperStatus } from "../api";
import { verdictTextClass } from "../verdict";
import { detectMarket, marketLabel, marketBadgeClass, marketAuthority } from "../market";
import { fmtMoney, fmtPct, fmtQty, isPresent, MISSING } from "../format";
import Segmented from "../components/Segmented";
import Panel from "../components/Panel";

const INPUT_CLASS =
  "w-full bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded px-3 py-2 text-[var(--color-text)] focus:border-[var(--color-accent)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]";

/** "5 of 5 passed", or the names of the checks that failed. Never a bare "Checked". */
function summarizeChecks(checks) {
  if (!checks || typeof checks !== "object") return MISSING;
  const entries = Object.entries(checks);
  if (entries.length === 0) return MISSING;
  const failed = entries.filter(([, ok]) => ok !== true).map(([name]) => name);
  if (failed.length === 0) return `${entries.length} of ${entries.length} passed`;
  return `failed: ${failed.join(", ")}`;
}

/** "0.40% of 1.00% limit" -- or "—" for whichever side is missing, never 0. */
function usageOfLimit(value, limit) {
  return `${fmtPct(value)} of ${fmtPct(limit)} limit`;
}

export default function TheDesk() {
  // Market & Screening links here as /dashboard?symbol=AMD&price=606.46 so a
  // candidate can be carried into a ticket without retyping it. Query params
  // rather than router state so the link survives a refresh and can be shared.
  //
  // This only pre-fills the form. Nothing here is an order: the ticket still
  // has to clear the Shariah, option-structure, account and risk gates at
  // /paper/preview, then be approved, then be executed with the confirmation
  // phrase. A prefilled symbol has cleared exactly nothing.
  //
  // Without query params the ticket starts empty. It used to default to AAPL at
  // 150.00 -- an invented price that a quick "Evaluate" would carry straight
  // into a real preview.
  const [searchParams] = useSearchParams();
  const [symbol, setSymbol] = useState((searchParams.get("symbol") || "").toUpperCase());
  const [side, setSide] = useState("BUY");
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState(searchParams.get("price") || "");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null);

  // What this instance will actually execute, per market. Read from the backend
  // rather than hardcoded -- the Trader card used to print "alpaca_mcp" and
  // "High — Live broker configuration" whatever the configuration was.
  const [paperStatus, setPaperStatus] = useState(null);
  const [paperStatusError, setPaperStatusError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchPaperStatus()
      .then((status) => !cancelled && setPaperStatus(status))
      .catch((err) => !cancelled && setPaperStatusError(err));
    return () => {
      cancelled = true;
    };
  }, []);

  // The gate's own answer once a preview exists; until then, a guess from the
  // symbol's shape. Never the other way round -- the authority decides which
  // market a symbol belongs to, this is only a pre-flight hint.
  const ticketMarket = preview?.agent_summary?.shariah?.market ?? detectMarket(symbol);

  const [isReviewing, setIsReviewing] = useState(false);
  const [reviewPreview, setReviewPreview] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const reviewHeadingRef = useRef(null);

  // The review replaces the verdict cards in place. Move focus to it, so a
  // keyboard or screen-reader user lands on what changed rather than on a
  // button that no longer exists; Escape backs out unless a submission is
  // already in flight.
  useEffect(() => {
    if (!isReviewing) return undefined;
    reviewHeadingRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape" && !isSubmitting) setIsReviewing(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isReviewing, isSubmitting]);
  const [submitError, setSubmitError] = useState(null);
  // What /paper/approval actually said. It answers HTTP 200 for a refusal too,
  // with the verdict in approval.status -- so this is read, never assumed.
  const [approvalResult, setApprovalResult] = useState(null);

  const qtyNumber = Number(qty);
  const priceNumber = Number(price);
  const ticketValid =
    symbol.trim() !== "" &&
    Number.isInteger(qtyNumber) &&
    qtyNumber > 0 &&
    Number.isFinite(priceNumber) &&
    priceNumber > 0;

  // position_pct / total_exposure_pct are advisory only: local_api.py's
  // apply_portfolio_risk_overlay recomputes the real projected exposure from
  // live portfolio state and that's what actually gates PASS/REJECT here.
  // loss_per_trade_pct is also computed by the risk evaluation based on
  // the specific ticket. We fetch real daily/weekly loss and order counts
  // from the backend so the preview doesn't show false safety margins.
  const getOrderData = (risk) => ({
    symbol: symbol.trim().toUpperCase(),
    side,
    quantity: qtyNumber,
    price: priceNumber,
    position_pct: 0,
    total_exposure_pct: 0,
    loss_per_trade_pct: 0,
    daily_loss_pct: risk?.daily_loss_pct || 0,
    orders_today: risk?.orders_today || 0
  });

  const handleEvaluate = async () => {
    if (!ticketValid) return;
    setLoading(true);
    setError(null);
    setIsReviewing(false);
    setApprovalResult(null);
    try {
      const risk = await fetchRiskSnapshot();
      const order = getOrderData(risk);
      const result = await fetchPreview(order);
      setPreview(result.preview);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleStartApproval = async () => {
    setIsReviewing(true);
    setReviewPreview(null);
    setSubmitError(null);
    try {
      const risk = await fetchRiskSnapshot();
      const order = getOrderData(risk);
      const result = await fetchPreview(order);
      setReviewPreview(result.preview);
    } catch (err) {
      setSubmitError("Failed to recompute the verdicts: " + err.message);
    }
  };

  const handleConfirmSubmit = async () => {
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const result = await submitApproval(reviewPreview, true);
      const approval = result?.approval || {};
      setApprovalResult({
        status: approval.status || "UNKNOWN",
        reason: approval.reason,
        // A refusal by the option-structure or account gate carries its own,
        // more specific reason one level down.
        detail: approval.option_structure?.reason || approval.account_shariah?.reason,
        queueId: result?.queue_id,
        symbol: reviewPreview?.symbol,
      });
      setIsReviewing(false);
      setPreview(null);
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const deriveShariahConfidence = (shariah) => {
    if (!shariah) return { conf: "Low", reason: "No data" };
    if (shariah.status === "UNKNOWN") return { conf: "Low", reason: "No authoritative data found" };
    if (shariah.provider === "SEC_EDGAR") return { conf: "High", reason: "Computed from 10-K ratios" };
    return { conf: "High", reason: "Exact match in active publication" };
  };

  const deriveQuantConfidence = (quant) => {
    if (!quant) return { conf: "Low", reason: "No data" };
    if (quant.bars < 200) return { conf: "Low", reason: `Insufficient history (${quant.bars} bars)` };
    if (quant.data_freshness === "stale") return { conf: "Low", reason: "Using stale cached data" };
    if (quant.data_freshness !== "live" && quant.data_freshness !== "cached") return { conf: "Low", reason: "Using synthetic fallback data, not live" };
    return { conf: "High", reason: "Live data, >200 bars" };
  };

  const deriveRiskConfidence = (risk) => {
    if (!risk) return { conf: "FAIL-CLOSED", reason: "Missing evaluation" };
    if (risk.status === 'REJECT' && (!risk.details?.limits || risk.reason?.includes('missing') || risk.reason?.includes('unavailable'))) {
      return { conf: "FAIL-CLOSED", reason: "Portfolio state or limits unavailable" };
    }
    return { conf: "High", reason: "Deterministic evaluation vs live portfolio" };
  };

  // A signal is not a ruling. BUY clears the quant leg; anything else is shown
  // as what it is. SELL used to be painted REJECT, which reads as a refusal.
  const quantVerdict = (signal) => (signal === "BUY" ? "PASS" : "UNKNOWN");

  // From GET /paper/status, for the market this ticket belongs to.
  const route = paperStatus?.execution_markets?.[ticketMarket];
  const executionCard = (() => {
    if (paperStatusError || !paperStatus) {
      return {
        adapter: MISSING,
        mode: MISSING,
        conf: "Low",
        reason: paperStatusError ? `Execution status unavailable: ${paperStatusError.message}` : "Execution status not loaded",
      };
    }
    const mode = paperStatus.live_trading === false ? "Paper only" : MISSING;
    if (!route) {
      return { adapter: MISSING, mode, conf: "Low", reason: `No execution route for ${marketLabel(ticketMarket)}` };
    }
    return {
      adapter: route.adapter || MISSING,
      mode,
      conf: route.enabled ? "High" : "Low",
      reason: route.enabled
        ? `Execution enabled for ${marketLabel(ticketMarket)} via ${route.adapter}`
        : `Execution not enabled for ${marketLabel(ticketMarket)}${route.reason ? ` (${route.reason})` : ""}`,
    };
  })();

  const blockerMessages = (p) => {
    const messages = new Map((p?.blocker_messages || []).map((m) => [m.blocker, m.message]));
    return (p?.blockers || []).map((blocker) => ({ blocker, message: messages.get(blocker) }));
  };

  const reviewRisk = reviewPreview?.agent_summary?.risk?.details;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">The Desk</h1>
      </div>

      <Panel>
        <h2 className="text-sm font-medium text-[var(--color-subtle)] uppercase tracking-wider mb-4">Ticket Entry</h2>

        {/* Four columns only from xl. At md this was ~180px per card, which is
            not enough for a role name and a verdict badge, and "Shariah
            Compliance Officer" wrapped to three lines while its badge overlapped
            it. */}
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label htmlFor="ticket-symbol" className="block text-sm text-[var(--color-muted)]">Symbol</label>
              {/* Which authority will screen this ticket. Before a preview runs
                  this is a local guess from the symbol's shape (see
                  src/market.js); once a preview exists the gate's own answer
                  replaces it. Styled neutrally on purpose -- the verdict
                  palette means permitted/refused, and "this is a Malaysian
                  stock" is neither. */}
              <span
                title={marketAuthority(ticketMarket)}
                className={`px-2 py-0.5 rounded text-xs font-bold ${marketBadgeClass()}`}
              >
                {marketLabel(ticketMarket)}
              </span>
            </div>
            <input
              id="ticket-symbol"
              type="text"
              value={symbol}
              placeholder="e.g. AAPL or 5225…"
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className={`${INPUT_CLASS} uppercase placeholder:normal-case tabular-nums`}
            />
            <p className="mt-1 text-xs text-[var(--color-muted)]">
              {marketAuthority(ticketMarket)}
            </p>
          </div>
          <div>
            <span id="ticket-side-label" className="block text-sm text-[var(--color-muted)] mb-1">Side</span>
            {/* Both sides visible at once, so the side is never a hidden dropdown
                value. Neutral styling on purpose: green/red here would borrow
                the verdict palette, which means permitted/refused. */}
            <Segmented
              label="Side"
              size="md"
              value={side}
              onChange={setSide}
              options={[["BUY", "BUY"], ["SELL", "SELL"]]}
            />
            <p className="mt-1 text-xs text-[var(--color-muted)]">
              {side === "SELL"
                ? "Reduces an existing position only — equity sells are reduce-only."
                : "Opens or adds to a long position."}
            </p>
          </div>
          <div>
            <label htmlFor="ticket-qty" className="block text-sm text-[var(--color-muted)] mb-1">Quantity (shares)</label>
            <input
              id="ticket-qty"
              type="number"
              min="1"
              step="1"
              inputMode="numeric"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              className={`${INPUT_CLASS} font-mono tabular-nums`}
            />
          </div>
          <div>
            <label htmlFor="ticket-price" className="block text-sm text-[var(--color-muted)] mb-1">Limit Price</label>
            <input
              id="ticket-price"
              type="number"
              min="0.01"
              step="0.01"
              inputMode="decimal"
              value={price}
              placeholder="Enter a limit price…"
              onChange={(e) => setPrice(e.target.value)}
              className={`${INPUT_CLASS} font-mono tabular-nums`}
            />
          </div>
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-between gap-4">
          {/* An estimate from the ticket's own numbers, before anything is sent.
              The preview's notional replaces it once the server has priced it. */}
          <div className="text-sm text-[var(--color-muted)]">
            Est. notional{" "}
            <span className="font-mono tabular-nums text-[var(--color-text)]">
              {ticketValid ? fmtMoney(qtyNumber * priceNumber, ticketMarket) : MISSING}
            </span>
          </div>
          <div className="flex items-center gap-4">
            {!ticketValid && (
              <span className="text-xs text-[var(--color-muted)]">
                Enter a symbol, a whole-share quantity and a positive limit price.
              </span>
            )}
            {/* The button names the order it will evaluate, so the side and size
                are confirmed at the moment of the click, not remembered. */}
            <button
              onClick={handleEvaluate}
              disabled={loading || !ticketValid}
              className="bg-[var(--color-accent)] text-[var(--color-button-text)] px-6 py-2 rounded font-medium hover:bg-[var(--color-accent-2)] transition-colors disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
            >
              {loading
                ? "Evaluating…"
                : ticketValid
                  ? `Evaluate ${side} ${fmtQty(qtyNumber)} ${symbol.trim().toUpperCase()}`
                  : "Evaluate Trade"}
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-4 p-3 bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)] rounded text-sm">
            {error}
          </div>
        )}

        {/* The approval outcome, as the server stated it. Approving queues an
            order; it never sends one. This banner used to say "Order
            successfully submitted to the paper broker" for every response --
            including a refusal, because a refusal also arrives as HTTP 200. */}
        <div aria-live="polite">
          {approvalResult && approvalResult.status === "APPROVED_PAPER_READY" && (
            <div className="mt-4 p-3 bg-[var(--color-ok-bg)] text-[var(--color-ok)] border border-[var(--color-ok)] rounded text-sm">
              <p className="font-medium">
                Approved and queued{approvalResult.queueId != null ? ` — #${approvalResult.queueId}` : ""}.
              </p>
              <p className="mt-1">
                Nothing has been sent to the broker. Execution is a separate step, performed by the
                operator relay with the EXECUTE PAPER confirmation — not from this page.
              </p>
            </div>
          )}
          {approvalResult && approvalResult.status === "REJECT" && (
            <div className="mt-4 p-3 bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)] rounded text-sm">
              <p className="font-medium">
                Refused at approval: {approvalResult.reason || "no reason given"}
                {approvalResult.detail && approvalResult.detail !== approvalResult.reason
                  ? ` (${approvalResult.detail})`
                  : ""}
              </p>
              <p className="mt-1">
                The server re-derived the verdicts and this order did not pass. It is recorded
                {approvalResult.queueId != null ? ` as #${approvalResult.queueId}` : ""} and cannot be executed.
              </p>
            </div>
          )}
          {approvalResult && !["APPROVED_PAPER_READY", "REJECT"].includes(approvalResult.status) && (
            <div className="mt-4 p-3 bg-[var(--color-unknown-bg)] text-[var(--color-unknown)] border border-[var(--color-unknown)] rounded text-sm">
              Approval returned {approvalResult.status}
              {approvalResult.reason ? `: ${approvalResult.reason}` : ""}. Nothing has been sent to the broker.
            </div>
          )}
        </div>
      </Panel>

      {preview && !isReviewing && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">

            <OfficerCard
              role="Quant Manager"
              title={`Signal for ${preview.symbol} • ${preview.side} ${preview.quantity} @ ${fmtMoney(preview.price, ticketMarket)}`}
              verdict={quantVerdict(preview.agent_summary?.quant?.signal)}
              verdictLabel={preview.agent_summary?.quant?.signal || 'N/A'}
              details={[
                { label: "Price Source", value: preview.agent_summary?.quant?.price_source || 'N/A' },
                { label: "Strategy", value: preview.agent_summary?.quant?.strategy?.strategy_id || 'N/A' },
                { label: "Reason", value: preview.agent_summary?.quant?.reason || '-' }
              ]}
              confidence={deriveQuantConfidence(preview.agent_summary?.quant).conf}
              confidenceReason={deriveQuantConfidence(preview.agent_summary?.quant).reason}
              sealText="Signal: BUY"
            />

            <OfficerCard
              role="Shariah Compliance Officer"
              /* Deliberately NOT "Ruling on AAPL - BUY 1 @ $150" like the quant and
                 risk cards. Those do evaluate this specific ticket; the Shariah
                 screen classifies the SECURITY. A Shariah reviewer was explicit
                 that the classification of a security must not be presented as
                 permissibility of a trading strategy, and a verdict captioned with
                 a side, quantity and price reads exactly like the latter. */
              title={`Classification of ${preview.symbol} as a security — not of this trade`}
              verdict={preview.agent_summary?.shariah?.status || 'UNKNOWN'}
              details={[
                { label: "Market", value: preview.agent_summary?.shariah?.market || 'N/A' },
                { label: "Provider", value: preview.agent_summary?.shariah?.provider || 'N/A' },
                { label: "Reason", value: preview.agent_summary?.shariah?.reason || '-' }
              ]}
              confidence={deriveShariahConfidence(preview.agent_summary?.shariah).conf}
              confidenceReason={deriveShariahConfidence(preview.agent_summary?.shariah).reason}
              sealText={`Security classified compliant — ${preview.agent_summary?.shariah?.provider || "authority"}`}
            />

            <OfficerCard
              role="Risk Manager"
              title={`Check on ${preview.symbol} • ${preview.side} ${preview.quantity} @ ${fmtMoney(preview.price, ticketMarket)}`}
              verdict={preview.agent_summary?.risk?.status || 'UNKNOWN'}
              details={[
                { label: "Notional", value: fmtMoney(preview.notional, ticketMarket) },
                { label: "Limit Checks", value: summarizeChecks(preview.agent_summary?.risk?.details?.checks) },
                { label: "Reason", value: preview.agent_summary?.risk?.reason || '-' }
              ]}
              confidence={deriveRiskConfidence(preview.agent_summary?.risk).conf}
              confidenceReason={deriveRiskConfidence(preview.agent_summary?.risk).reason}
              sealText="Within configured risk limits"
            />

            <OfficerCard
              role="Trader - Execution Desk"
              title={`Execution route for ${preview.symbol}`}
              verdict={preview.status === 'READY_FOR_APPROVAL' ? 'UNKNOWN' : preview.status === 'BLOCKED' ? 'REJECT' : 'UNKNOWN'}
              verdictLabel={preview.status === 'READY_FOR_APPROVAL' ? 'AWAITING APPROVAL' : preview.status}
              details={[
                { label: "Adapter", value: executionCard.adapter },
                { label: "Mode", value: executionCard.mode },
                { label: "Executed by", value: "Operator relay" }
              ]}
              confidenceTitle="Execution Route"
              confidence={executionCard.conf}
              confidenceReason={executionCard.reason}
            />

          </div>

          <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-6 flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
             <div className="text-[var(--color-text)] min-w-0">
                <p className="font-serif text-lg font-bold">
                  {preview.status === "READY_FOR_APPROVAL" ? "Ready for approval" : "Blocked"}
                </p>
                <p className="text-sm text-[var(--color-muted)] mt-1">
                  Server preview. Approval re-derives the Shariah and risk verdicts on the server,
                  so nothing on this screen is final.
                </p>
                {preview.blockers?.length > 0 && (
                  <ul className="mt-3 space-y-1 text-sm">
                    {blockerMessages(preview).map(({ blocker, message }) => (
                      <li key={blocker} className="text-[var(--color-bad)]">
                        <span className="font-mono">{blocker}</span>
                        {message ? <span className="text-[var(--color-text)]"> — {message}</span> : null}
                      </li>
                    ))}
                  </ul>
                )}
             </div>
             {preview.status === 'READY_FOR_APPROVAL' && (
               <button
                 onClick={handleStartApproval}
                 className="shrink-0 bg-[var(--color-ok)] text-[var(--color-button-text)] px-8 py-3 rounded font-bold uppercase tracking-wider hover:opacity-90 transition-opacity focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
               >
                 Review for Approval
               </button>
             )}
          </div>

        </div>
      )}

      {isReviewing && (
        <section
          aria-labelledby="review-heading"
          className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded-md shadow-md p-6 max-w-3xl mx-auto space-y-6"
        >
          <h2
            id="review-heading"
            ref={reviewHeadingRef}
            tabIndex={-1}
            className="text-xl font-serif font-bold text-[var(--color-text)] mb-2 focus:outline-none"
          >
            Final Review &amp; Approval
          </h2>

          {submitError && <ErrorNote what="the approval" error={submitError} />}

          {!reviewPreview ? (
            !submitError && (
              <div className="text-[var(--color-muted)] text-sm animate-pulse">
                Recomputing verdicts…
              </div>
            )
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Order Details</span>
                  <div className="font-mono text-[var(--color-text)] tabular-nums">
                    {reviewPreview.symbol} • {reviewPreview.side} • {reviewPreview.quantity} share(s) @ {fmtMoney(reviewPreview.price, ticketMarket)}
                  </div>
                  <div className="font-mono text-[var(--color-muted)] tabular-nums text-xs mt-1">
                    notional {fmtMoney(reviewPreview.notional, ticketMarket)}
                  </div>
                </div>

                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Security Shariah Classification <span className="text-[var(--color-accent)] ml-2">(recomputed just now)</span></span>
                  <div className="text-[var(--color-text)]">
                    <span className={`font-bold ${verdictTextClass(reviewPreview.agent_summary?.shariah?.status)}`}>
                      {reviewPreview.agent_summary?.shariah?.status || MISSING}
                    </span>
                    <span className="mx-2">—</span>
                    <span className="text-sm">{reviewPreview.agent_summary?.shariah?.provider || MISSING}</span>
                  </div>
                  {/* Stated at the point of approval, where the confusion would
                      actually cost something. The authority classifies the
                      security; it certifies neither this trade nor this system. */}
                  <p className="text-xs text-[var(--color-muted)] mt-2 leading-snug">
                    Classifies the security only. Not a ruling on this trade, this
                    strategy, or this system.
                  </p>
                </div>

                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Risk Verdict <span className="text-[var(--color-accent)] ml-2">(recomputed just now)</span></span>
                  <div className="text-[var(--color-text)]">
                    <span className={`font-bold ${verdictTextClass(reviewPreview.agent_summary?.risk?.status)}`}>
                      {reviewPreview.agent_summary?.risk?.status || MISSING}
                    </span>
                    <span className="mx-2">—</span>
                    <span className="font-mono tabular-nums">
                      position {usageOfLimit(reviewRisk?.portfolio?.projected_position_pct, reviewRisk?.portfolio?.limits?.max_position_pct)}
                    </span>
                  </div>
                </div>

                {/* Each loss figure against its own limit. This used to compare
                    the weekly loss with the DAILY limit. */}
                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Loss Limits</span>
                  <div className="text-[var(--color-text)] font-mono tabular-nums space-y-0.5">
                    <div>daily {usageOfLimit(reviewRisk?.daily_loss_pct, reviewRisk?.limits?.max_daily_loss_pct)}</div>
                    <div>weekly {usageOfLimit(reviewRisk?.weekly_loss_pct, reviewRisk?.limits?.max_weekly_loss_pct)}</div>
                    <div>
                      orders today {isPresent(reviewRisk?.orders_today) ? reviewRisk.orders_today : MISSING} of{" "}
                      {isPresent(reviewRisk?.limits?.max_orders_per_day) ? reviewRisk.limits.max_orders_per_day : MISSING}
                    </div>
                  </div>
                </div>

                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)] sm:col-span-2">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">What approving does</span>
                  <div className="text-[var(--color-text)]">
                    Records your approval in the queue, after the server re-derives every verdict.
                    Nothing is sent to the broker from this page — execution is a separate step,
                    performed by the operator relay with the EXECUTE PAPER confirmation.
                  </div>
                </div>
              </div>

              <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-[var(--color-border)]">
                <button
                  onClick={() => setIsReviewing(false)}
                  disabled={isSubmitting}
                  className="px-6 py-2 rounded font-medium border border-[var(--color-border-strong)] text-[var(--color-text)] hover:bg-[var(--color-bg)] transition-colors focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmSubmit}
                  disabled={isSubmitting || reviewPreview.status !== 'READY_FOR_APPROVAL'}
                  className="bg-[var(--color-ok)] text-[var(--color-button-text)] px-8 py-2 rounded font-bold hover:opacity-90 transition-opacity disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
                >
                  {isSubmitting ? "Approving…" : "Approve"}
                </button>
              </div>
            </div>
          )}
          {submitError && (
            <div className="flex justify-end">
              <button
                onClick={() => setIsReviewing(false)}
                className="px-6 py-2 rounded font-medium border border-[var(--color-border-strong)] text-[var(--color-text)] hover:bg-[var(--color-bg)] transition-colors"
              >
                Close
              </button>
            </div>
          )}
        </section>
      )}

      {/* The record of what this system has already decided about this security,
          approvals and refusals alike. Shown after a preview rather than on an
          empty ticket: before evaluating, the symbol in the box is a guess, and
          fetching a trail for every keystroke would be noise. */}
      {preview ? (
        <Panel>
          <h2 className="text-sm font-medium text-[var(--color-subtle)] uppercase tracking-wider mb-1">
            Decision Trail
          </h2>
          <p className="text-xs text-[var(--color-muted)] mb-4">
            Append-only. Each entry records which authority ruled, on which document, and on
            which prices &mdash; so a verdict can be checked rather than taken on trust.
          </p>
          <EvidenceTrail ticker={(preview.symbol || symbol).toUpperCase()} />
        </Panel>
      ) : null}
    </div>
  );
}
