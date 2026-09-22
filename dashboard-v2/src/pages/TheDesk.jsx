import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import OfficerCard from "../components/OfficerCard";
import EvidenceTrail from "../components/EvidenceTrail";
import { fetchPreview, submitApproval, fetchRiskSnapshot } from "../api";
import { verdictTextClass } from "../verdict";
import { detectMarket, marketLabel, marketBadgeClass, marketAuthority, formatPrice } from "../market";

export default function TheDesk() {
  // Market & Screening links here as /dashboard?symbol=AMD&price=606.46 so a
  // candidate can be carried into a ticket without retyping it. Query params
  // rather than router state so the link survives a refresh and can be shared.
  //
  // This only pre-fills the form. Nothing here is an order: the ticket still
  // has to clear the Shariah, option-structure, account and risk gates at
  // /paper/preview, then be approved, then be executed with the confirmation
  // phrase. A prefilled symbol has cleared exactly nothing.
  const [searchParams] = useSearchParams();
  const initialSymbol = (searchParams.get("symbol") || "AAPL").toUpperCase();
  const initialPrice = Number(searchParams.get("price")) || 150.0;

  const [symbol, setSymbol] = useState(initialSymbol);
  const [side, setSide] = useState("BUY");
  const [qty, setQty] = useState(1);
  const [price, setPrice] = useState(initialPrice);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null);

  // The gate's own answer once a preview exists; until then, a guess from the
  // symbol's shape. Never the other way round -- the authority decides which
  // market a symbol belongs to, this is only a pre-flight hint.
  const ticketMarket = preview?.agent_summary?.shariah?.market ?? detectMarket(symbol);

  const [isReviewing, setIsReviewing] = useState(false);
  const [reviewPreview, setReviewPreview] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  // position_pct / total_exposure_pct are advisory only: local_api.py's
  // apply_portfolio_risk_overlay recomputes the real projected exposure from
  // live portfolio state and that's what actually gates PASS/REJECT here.
  // loss_per_trade_pct is also computed by the risk evaluation based on
  // the specific ticket. We fetch real daily/weekly loss and order counts
  // from the backend so the preview doesn't show false safety margins.
  const getOrderData = (risk) => ({
    symbol: symbol.toUpperCase(),
    side,
    quantity: qty,
    price,
    position_pct: 0,
    total_exposure_pct: 0,
    loss_per_trade_pct: 0,
    daily_loss_pct: risk?.daily_loss_pct || 0,
    orders_today: risk?.orders_today || 0
  });

  const handleEvaluate = async () => {
    setLoading(true);
    setError(null);
    setIsReviewing(false);
    setSubmitSuccess(false);
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
    setSubmitError(null);
    try {
      const risk = await fetchRiskSnapshot();
      const order = getOrderData(risk);
      const result = await fetchPreview(order);
      setReviewPreview(result.preview);
    } catch (err) {
      setSubmitError("Failed to recompute authoritative verdict: " + err.message);
    }
  };

  const handleConfirmSubmit = async () => {
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      await submitApproval(reviewPreview, true);
      setSubmitSuccess(true);
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">The Desk</h1>
        <div className="flex gap-3">
          <div className="px-3 py-1 rounded bg-[var(--color-unknown-bg)] text-[var(--color-unknown)] text-sm font-medium border border-[var(--color-unknown)]">
            Preview Mode
          </div>
          <div className="px-3 py-1 rounded bg-[var(--color-ok-bg)] text-[var(--color-ok)] text-sm font-medium border border-[var(--color-ok)]">
            Local Agents
          </div>
        </div>
      </div>

      <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-6">
        <h2 className="text-sm font-medium text-[var(--color-subtle)] uppercase tracking-wider mb-4">Ticket Entry</h2>
        
        {/* Four columns only from xl. At md this was ~180px per card, which is
            not enough for a role name and a verdict badge, and "Shariah
            Compliance Officer" wrapped to three lines while its badge overlapped
            it. */}
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block text-sm text-[var(--color-muted)]">Symbol</label>
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
              type="text"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded px-3 py-2 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)] uppercase tabular-nums"
            />
            <p className="mt-1 text-xs text-[var(--color-muted)]">
              {marketAuthority(ticketMarket)}
            </p>
          </div>
          <div>
            <label className="block text-sm text-[var(--color-muted)] mb-1">Side</label>
            <select 
              value={side}
              onChange={(e) => setSide(e.target.value)}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded px-3 py-2 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>
          <div>
            <label className="block text-sm text-[var(--color-muted)] mb-1">Quantity</label>
            <input 
              type="number" 
              value={qty}
              onChange={(e) => setQty(Number(e.target.value))}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded px-3 py-2 text-[var(--color-text)] font-mono tabular-nums focus:outline-none focus:border-[var(--color-accent)]"
            />
          </div>
          <div>
            <label className="block text-sm text-[var(--color-muted)] mb-1">Limit Price</label>
            <input 
              type="number" 
              value={price}
              onChange={(e) => setPrice(Number(e.target.value))}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded px-3 py-2 text-[var(--color-text)] font-mono tabular-nums focus:outline-none focus:border-[var(--color-accent)]"
            />
          </div>
        </div>

        <div className="mt-6 flex justify-end">
          <button 
            onClick={handleEvaluate}
            disabled={loading}
            className="bg-[var(--color-accent)] text-[var(--color-button-text)] px-6 py-2 rounded font-medium hover:bg-[var(--color-accent-2)] transition-colors disabled:opacity-50"
          >
            {loading ? "Evaluating..." : "Evaluate Trade"}
          </button>
        </div>
        
        {error && (
          <div className="mt-4 p-3 bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)] rounded text-sm">
            {error}
          </div>
        )}
        
        {submitSuccess && (
          <div className="mt-4 p-3 bg-[var(--color-ok-bg)] text-[var(--color-ok)] border border-[var(--color-ok)] rounded text-sm font-medium">
            Order successfully submitted to the paper broker.
          </div>
        )}
      </div>

      {preview && !isReviewing && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            
            <OfficerCard 
              role="Quant Manager"
              title={`Ruling on ${preview.symbol} • ${preview.side} ${preview.quantity} @ ${formatPrice(preview.price, ticketMarket)}`}
              verdict={preview.agent_summary?.quant?.signal === 'BUY' ? 'PASS' : preview.agent_summary?.quant?.signal === 'NO_SIGNAL' ? 'UNKNOWN' : 'REJECT'}
              verdictLabel={preview.agent_summary?.quant?.signal || 'N/A'}
              details={[
                { label: "Price Source", value: preview.agent_summary?.quant?.price_source || 'N/A' },
                { label: "Strategy", value: preview.agent_summary?.quant?.strategy?.strategy_id || 'N/A' },
                { label: "Reason", value: preview.agent_summary?.quant?.reason || '-' }
              ]}
              confidence={deriveQuantConfidence(preview.agent_summary?.quant).conf}
              confidenceReason={deriveQuantConfidence(preview.agent_summary?.quant).reason}
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
            />

            <OfficerCard 
              role="Risk Manager"
              title={`Ruling on ${preview.symbol} • ${preview.side} ${preview.quantity} @ ${formatPrice(preview.price, ticketMarket)}`}
              verdict={preview.agent_summary?.risk?.status || 'UNKNOWN'}
              details={[
                { label: "Position Size", value: `${(preview.notional || 0)}` },
                { label: "Daily Loss Limit", value: "Checked" },
                { label: "Reason", value: preview.agent_summary?.risk?.reason || '-' }
              ]}
              confidence={deriveRiskConfidence(preview.agent_summary?.risk).conf}
              confidenceReason={deriveRiskConfidence(preview.agent_summary?.risk).reason}
            />

            <OfficerCard 
              role="Trader - Execution Desk"
              title={`Execution preview for ${preview.symbol}`}
              verdict={preview.status || 'UNKNOWN'}
              verdictLabel={preview.status === 'READY_FOR_APPROVAL' ? 'PENDING_APPROVAL' : preview.status}
              details={[
                { label: "Adapter", value: "alpaca_mcp" },
                { label: "Mode", value: "Paper" },
                { label: "Blockers", value: preview.blockers?.length ? preview.blockers.join(", ") : "None" }
              ]}
              confidenceTitle="Execution Readiness"
              confidence="High"
              confidenceReason="Live broker configuration"
            />

          </div>

          <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-6 flex items-center justify-between">
             <div className="text-[var(--color-text)]">
                <p className="font-serif text-lg font-bold">Preview Verdict: {preview.status}</p>
                <p className="text-sm text-[var(--color-muted)] mt-1">
                   This is a client-side preview. The final risk and shariah verdicts will be re-derived securely at approval time.
                </p>
             </div>
             {preview.status === 'READY_FOR_APPROVAL' && (
               <button 
                 onClick={handleStartApproval}
                 className="bg-[var(--color-ok)] text-[var(--color-button-text)] px-8 py-3 rounded font-bold uppercase tracking-wider hover:opacity-90 transition-opacity"
               >
                 Submit for Approval
               </button>
             )}
          </div>

        </div>
      )}

      {isReviewing && (
        <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded-md shadow-md p-6 max-w-3xl mx-auto space-y-6">
          <h2 className="text-xl font-serif font-bold text-[var(--color-text)] mb-2">Final Review & Approval</h2>
          
          {submitError && (
            <div className="p-3 bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)] rounded text-sm">
              {submitError}
            </div>
          )}

          {!reviewPreview ? (
            <div className="text-[var(--color-muted)] text-sm animate-pulse">
              Recomputing authoritative verdicts...
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Order Details</span>
                  <div className="font-mono text-[var(--color-text)] tabular-nums">
                    {reviewPreview.symbol} • {reviewPreview.side} • {reviewPreview.quantity} share(s)
                  </div>
                </div>
                
                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Security Shariah Classification <span className="text-[var(--color-accent)] ml-2">(recomputed just now)</span></span>
                  <div className="text-[var(--color-text)]">
                    <span className={`font-bold ${verdictTextClass(reviewPreview.agent_summary?.shariah?.status)}`}>
                      {reviewPreview.agent_summary?.shariah?.status}
                    </span>
                    <span className="mx-2">—</span>
                    <span className="text-sm">{reviewPreview.agent_summary?.shariah?.provider}</span>
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
                      {reviewPreview.agent_summary?.risk?.status}
                    </span>
                    <span className="mx-2">—</span>
                    <span className="font-mono tabular-nums">position {reviewPreview.agent_summary?.risk?.details?.portfolio?.projected_position_pct?.toFixed(2) || '0.00'}% of {reviewPreview.agent_summary?.risk?.details?.portfolio?.limits?.max_position_pct?.toFixed(2) || '0.00'}% limit</span>
                  </div>
                </div>

                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Loss Limits</span>
                  <div className="text-[var(--color-text)] font-mono tabular-nums">
                    Weekly loss: {reviewPreview.agent_summary?.risk?.details?.weekly_loss_pct || 0}% of {reviewPreview.agent_summary?.risk?.details?.portfolio?.limits?.max_daily_loss_pct || 0}% limit
                  </div>
                </div>

                <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-bg)]">
                  <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Action</span>
                  <div className="text-[var(--color-text)]">
                    Submits to the paper broker immediately — not reversible from here.
                  </div>
                </div>
              </div>

              <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-[var(--color-border)]">
                <button 
                  onClick={() => setIsReviewing(false)}
                  disabled={isSubmitting}
                  className="px-6 py-2 rounded font-medium border border-[var(--color-border-strong)] text-[var(--color-text)] hover:bg-[var(--color-bg)] transition-colors"
                >
                  Cancel
                </button>
                <button 
                  onClick={handleConfirmSubmit}
                  disabled={isSubmitting || reviewPreview.status !== 'READY_FOR_APPROVAL'}
                  className="bg-[var(--color-ok)] text-[var(--color-button-text)] px-8 py-2 rounded font-bold hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  {isSubmitting ? "Submitting..." : "Confirm & Submit"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* The record of what this system has already decided about this security,
          approvals and refusals alike. Shown after a preview rather than on an
          empty ticket: before evaluating, the symbol in the box is a guess, and
          fetching a trail for every keystroke would be noise. */}
      {preview ? (
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-6">
          <h2 className="text-sm font-medium text-[var(--color-subtle)] uppercase tracking-wider mb-1">
            Decision Trail
          </h2>
          <p className="text-xs text-[var(--color-muted)] mb-4">
            Append-only. Each entry records which authority ruled, on which document, and on
            which prices &mdash; so a verdict can be checked rather than taken on trust.
          </p>
          <EvidenceTrail ticker={(preview.symbol || symbol).toUpperCase()} />
        </div>
      ) : null}
    </div>
  );
}

