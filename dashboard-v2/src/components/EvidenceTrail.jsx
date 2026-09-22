/**
 * The proving half, made visible.
 *
 * The project's claim is that the gate chain *enforces and proves*. Enforcement
 * was always visible -- an order that fails a gate does not go through. The
 * proof was not: `evidence.py` wrote an append-only trail, `GET
 * /api/evidence/{ticker}` served it, and no screen in this dashboard read it.
 * A reader had to curl the API to see the evidence the project is named for.
 *
 * What makes a record evidence rather than a log line is provenance, so that is
 * what this shows: which authority ruled, the hash of the document it ruled
 * from, which feed priced it and as of when. A row saying "BLOCKED" proves
 * nothing; a row saying "BLOCKED because the SC list at hash d6592a55… does not
 * contain this security, priced from yahoo as of 2026-09-18" is a claim someone
 * can check.
 *
 * Refusals are shown exactly like approvals. "Your system let this through" and
 * "your system stopped this" both need evidence, and the refusals are the ones
 * more likely to be disputed.
 */

import { useEffect, useState } from "react";
import { fetchEvidence } from "../api";
import { verdictBadgeClass } from "../verdict";

/** A decision outcome is not a Shariah verdict -- map it deliberately rather
 *  than passing it to verdictStyle, which would silently render every
 *  READY_FOR_APPROVAL as UNKNOWN. */
const DECISION_VERDICT = {
  READY_FOR_APPROVAL: "PASS",
  BLOCKED: "REJECT",
};

function decisionTone(decision) {
  return DECISION_VERDICT[decision] ?? "UNKNOWN";
}

function Field({ label, value, mono = false }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div>
      <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-[10px] font-bold">
        {label}
      </span>
      <span
        className={
          mono
            ? "text-xs text-[var(--color-text)] font-mono break-all"
            : "text-xs text-[var(--color-text)]"
        }
      >
        {value}
      </span>
    </div>
  );
}

function DecisionCard({ record }) {
  const tone = decisionTone(record.decision);
  const shariah = record.shariah || {};
  const market = record.market_data || {};
  const quant = record.quant || {};

  return (
    <div className="border border-[var(--color-border)] rounded-md bg-[var(--color-panel-2)] p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-bold ${verdictBadgeClass(tone)}`}>
            {record.decision}
          </span>
          {/* A background scan is not an order. Without this the trail reads as
              though the operator considered every watchlist symbol. */}
          <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-[var(--color-panel-3)] text-[var(--color-subtle)] border border-[var(--color-border)]">
            {record.source === "preview" ? "order preview" : record.source || "unknown"}
          </span>
        </div>
        <span className="text-xs text-[var(--color-muted)] font-mono">
          {record.timestamp ? record.timestamp.replace("T", " ").slice(0, 19) : "—"}
        </span>
      </div>

      <p className="text-sm text-[var(--color-text)]">
        <span className="text-[var(--color-muted)]">Reason: </span>
        {record.decision_reason || "—"}
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-1">
        <Field label="Shariah" value={shariah.status} />
        <Field label="Authority" value={shariah.provider} />
        <Field label="Publication" value={shariah.publication_id} />
        <Field label="Quant" value={quant.signal} />
        <Field label="Price" value={market.price ?? quant.price} />
        <Field label="Feed" value={market.price_source || quant.price_source} />
        <Field label="Data as of" value={market.as_of_date || quant.as_of_date} />
        <Field label="Freshness" value={market.data_freshness} />
        <Field label="Bars" value={quant.bars} />
      </div>

      {shariah.source_document_hash ? (
        <div className="pt-1 border-t border-[var(--color-border)]">
          {/* The hash is what makes this checkable rather than merely stated. */}
          <Field label="Source document sha256" value={shariah.source_document_hash} mono />
        </div>
      ) : null}
    </div>
  );
}

export default function EvidenceTrail({ ticker }) {
  // One state object carrying which ticker it describes, so "still loading" is
  // derived during render rather than set synchronously at the top of the
  // effect. That also removes a real bug the simpler shape has: switching
  // tickers would briefly render the previous symbol's trail under the new
  // symbol's heading.
  const [state, setState] = useState({ ticker: null, data: null, error: null });

  useEffect(() => {
    if (!ticker) return undefined;
    let cancelled = false;
    fetchEvidence(ticker)
      .then((result) => {
        if (!cancelled) setState({ ticker, data: result, error: null });
      })
      .catch((err) => {
        if (!cancelled) setState({ ticker, data: null, error: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  const loading = state.ticker !== ticker;
  const { data, error } = state;

  if (!ticker) return null;
  if (loading) {
    return (
      <div className="text-[var(--color-muted)] animate-pulse text-sm">
        Loading the decision trail...
      </div>
    );
  }
  if (error) {
    return (
      <div className="text-[var(--color-bad)] p-3 bg-[var(--color-bad-bg)] rounded text-sm">
        {error}
      </div>
    );
  }

  const decisions = data?.decisions ?? [];

  if (!decisions.length) {
    // Deliberately not "no evidence found", which sounds like a verdict. Nothing
    // has been decided about this security yet; that is a fact about us.
    return (
      <div className="p-4 rounded border border-[var(--color-border)] bg-[var(--color-panel-2)]">
        <p className="text-sm text-[var(--color-muted)]">
          No decision has been recorded for {ticker} yet. The trail records an entry each time
          this security is evaluated &mdash; whether the order was approved or refused.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--color-muted)]">
        {decisions.length} decision{decisions.length === 1 ? "" : "s"} recorded for {ticker},
        newest first. Every entry is append-only and carries the authority and prices it was
        decided on.
      </p>
      {decisions.map((record) => (
        <DecisionCard key={record.decision_id} record={record} />
      ))}
    </div>
  );
}
