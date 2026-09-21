import React from "react";
import { CheckCircle, AlertTriangle, XCircle, HelpCircle } from "lucide-react";
import { verdictStyle } from "../verdict";

// Unrecognised verdicts get the question mark, matching verdict.js failing
// closed to UNKNOWN rather than to anything reassuring.
const VERDICT_ICONS = {
  PASS: CheckCircle,
  WARN: AlertTriangle,
  REJECT: XCircle,
  UNKNOWN: HelpCircle,
};

export default function OfficerCard({ 
  role, 
  title,
  verdict, 
  verdictLabel,
  details, 
  confidence, 
  confidenceTitle,
  confidenceReason 
}) {
  const styles = verdictStyle(verdict);
  const VerdictIcon = VERDICT_ICONS[verdict] ?? HelpCircle;
  const isFailClosed = confidence === 'FAIL-CLOSED';

  return (
    <div className={`bg-[var(--color-panel-2)] border rounded-md flex flex-col min-h-48 shadow-sm ${
      isFailClosed ? 'border-[var(--color-bad)] ring-1 ring-[var(--color-bad)]' : 'border-[var(--color-border)]'
    }`}>
      {/* Badge stacked above the title rather than beside it. Side by side, a
          long role and a long verdict ("Trader - Execution Desk" +
          PENDING_APPROVAL) competed for a ~200px column and visibly overlapped.
          Stacking removes the competition and makes every header identical in
          structure; min-h keeps them aligned when role names differ in length. */}
      <div className="p-4 min-h-[6.5rem] border-b border-[var(--color-border)] flex flex-col gap-2 bg-[var(--color-panel-3)] rounded-t-md">
        <div className={`self-start max-w-full px-2.5 py-1 rounded flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider ${styles.badge}`}>
          <VerdictIcon className={`w-3.5 h-3.5 shrink-0 ${styles.icon}`} />
          <span className="truncate">{verdictLabel || verdict}</span>
        </div>
        <div className="min-w-0">
          <h3 className="font-serif font-bold text-base leading-snug text-[var(--color-text)]">{role}</h3>
          <p className="text-xs font-sans text-[var(--color-muted)] mt-0.5 leading-snug">{title}</p>
        </div>
      </div>

      {/* Values are snake_case identifiers with no spaces to break on
          (pullback_in_uptrend_confirmed, risk_limits_passed), so they used to
          run straight out of the card. min-w-0 lets the flex child shrink and
          break-words lets an unbroken token wrap rather than overflow. */}
      <div className="p-4 flex-1 font-mono text-xs text-[var(--color-subtle)] space-y-2.5">
        {details.map((detail, idx) => (
          <div key={idx} className="flex justify-between items-baseline gap-3">
            <span className="text-[var(--color-muted)] shrink-0">{detail.label}:</span>
            <span className="min-w-0 text-[var(--color-text)] font-medium text-right leading-relaxed break-words">
              {detail.value}
            </span>
          </div>
        ))}
      </div>

      {/* mt-auto pins the whole bottom block, so footers line up across the row
          whether or not a card carries a stamp. */}
      <div className="mt-auto">
        <div className="px-4 py-3 border-t border-[var(--color-border)] bg-[var(--color-panel)] space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-muted)] font-bold">{confidenceTitle || "Confidence"}</span>
            <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-sm ${
              confidence === 'High'
                ? 'bg-[var(--color-ok-bg)] text-[var(--color-ok)]'
                : confidence === 'FAIL-CLOSED'
                ? 'bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)]'
                : 'bg-[var(--color-warn-bg)] text-[var(--color-warn)]'
            }`}>
              {confidence}
            </span>
          </div>
          {/* On its own line rather than squeezed beside the badge. */}
          <span className={`block text-[11px] leading-snug ${confidence === 'FAIL-CLOSED' ? 'text-[var(--color-bad)] font-bold' : 'text-[var(--color-muted)]'}`}>
            {confidenceReason}
          </span>
        </div>

        {verdict === 'PASS' && (
          <div className="px-4 pb-4 pt-3 bg-[var(--color-panel)] rounded-b-md">
            <div className="flex items-center gap-2 text-[11px] font-serif text-[var(--color-accent)] border border-[var(--color-accent-muted)] bg-[var(--color-bg-soft)] px-3 py-2 rounded leading-snug">
              <span className="text-base shrink-0">⚖</span>
              <span>Stamped &amp; Approved by {role}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
