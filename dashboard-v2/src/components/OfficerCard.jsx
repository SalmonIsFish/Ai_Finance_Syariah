import React from "react";
import { CheckCircle, AlertTriangle, XCircle, HelpCircle } from "lucide-react";

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
  const getVerdictStyles = () => {
    switch (verdict) {
      case "PASS":
        return { 
          badge: "bg-[var(--color-ok-bg)] text-[var(--color-ok)] border border-[var(--color-ok)]",
          icon: <CheckCircle className="w-5 h-5 text-[var(--color-ok)]" />
        };
      case "WARN":
        return { 
          badge: "bg-[var(--color-warn-bg)] text-[var(--color-warn)] border border-[var(--color-warn)]",
          icon: <AlertTriangle className="w-5 h-5 text-[var(--color-warn)]" />
        };
      case "REJECT":
        return { 
          badge: "bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)]",
          icon: <XCircle className="w-5 h-5 text-[var(--color-bad)]" />
        };
      case "UNKNOWN":
      default:
        return { 
          badge: "bg-[var(--color-unknown-bg)] text-[var(--color-unknown)] border border-[var(--color-unknown)]",
          icon: <HelpCircle className="w-5 h-5 text-[var(--color-unknown)]" />
        };
    }
  };

  const styles = getVerdictStyles();
  const isFailClosed = confidence === 'FAIL-CLOSED';

  return (
    <div className={`bg-[var(--color-panel-2)] border rounded-md flex flex-col min-h-48 shadow-sm ${
      isFailClosed ? 'border-[var(--color-bad)] ring-1 ring-[var(--color-bad)]' : 'border-[var(--color-border)]'
    }`}>
      <div className="p-4 border-b border-[var(--color-border)] flex items-start justify-between bg-[var(--color-panel-3)] rounded-t-md">
        <div>
          <h3 className="font-serif font-bold text-lg text-[var(--color-text)]">{role}</h3>
          <p className="text-sm font-sans text-[var(--color-muted)] mt-1">{title}</p>
        </div>
        <div className={`px-3 py-1 rounded flex items-center gap-2 text-sm font-bold uppercase tracking-wider ${styles.badge}`}>
          {styles.icon}
          {verdictLabel || verdict}
        </div>
      </div>
      
      <div className="p-4 flex-1 flex flex-col font-mono text-sm text-[var(--color-subtle)] space-y-2">
        {details.map((detail, idx) => (
          <div key={idx} className="flex justify-between">
            <span className="text-[var(--color-muted)]">{detail.label}:</span>
            <span className="text-[var(--color-text)] font-medium text-right max-w-[60%] leading-tight">{detail.value}</span>
          </div>
        ))}
      </div>

      <div className="p-4 border-t border-[var(--color-border)] bg-[var(--color-panel)] rounded-b-md flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wider text-[var(--color-muted)] font-bold">{confidenceTitle || "Confidence"}</span>
          <span className={`text-xs uppercase tracking-wider font-bold px-2 py-0.5 rounded-sm ${
            confidence === 'High' 
              ? 'bg-[var(--color-ok-bg)] text-[var(--color-ok)]' 
              : confidence === 'FAIL-CLOSED'
              ? 'bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)]'
              : 'bg-[var(--color-warn-bg)] text-[var(--color-warn)]'
          }`}>
            {confidence}
          </span>
        </div>
        <span className={`text-xs ${confidence === 'FAIL-CLOSED' ? 'text-[var(--color-bad)] font-bold' : 'text-[var(--color-muted)]'}`}>
          {confidenceReason}
        </span>
      </div>

      {verdict === 'PASS' && (
        <div className="px-4 pb-4 bg-[var(--color-panel)] rounded-b-md">
          <div className="flex items-center gap-2 text-xs font-serif text-[var(--color-accent)] border border-[var(--color-accent-muted)] bg-[var(--color-bg-soft)] px-3 py-2 rounded">
            <span className="text-lg">⚖</span>
            <span>Stamped & Approved by {role}</span>
          </div>
        </div>
      )}
    </div>
  );
}
