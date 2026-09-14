import { useState, useEffect } from "react";
import { fetchApprovals, fetchExecutionAudit, fetchAuditEvents } from "../api";

export default function TheLedger() {
  const [data, setData] = useState({ approvals: null, execution: null, auditEvents: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const [approvalsResult, executionResult, auditEventsResult] = await Promise.allSettled([
          fetchApprovals(),
          fetchExecutionAudit(),
          fetchAuditEvents()
        ]);
        
        let approvals = approvalsResult.status === "fulfilled" ? approvalsResult.value : null;
        if (approvalsResult.status === "rejected") console.error("fetchApprovals failed:", approvalsResult.reason);

        let execution = executionResult.status === "fulfilled" ? executionResult.value : null;
        if (executionResult.status === "rejected") console.error("fetchExecutionAudit failed:", executionResult.reason);

        let auditEvents = auditEventsResult.status === "fulfilled" ? auditEventsResult.value : null;
        if (auditEventsResult.status === "rejected") console.error("fetchAuditEvents failed:", auditEventsResult.reason);

        setData({ approvals, execution, auditEvents });
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <div className="text-[var(--color-muted)] animate-pulse">Loading The Ledger...</div>;
  if (error) return <div className="text-[var(--color-bad)] p-4 bg-[var(--color-bad-bg)] rounded">{error}</div>;

  const { approvals, execution, auditEvents } = data;

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">The Ledger</h1>
      </div>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Approval Queue (History)</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm overflow-x-auto max-h-96">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider sticky top-0">
              <tr>
                <th className="px-4 py-3 font-bold">Time</th>
                <th className="px-4 py-3 font-bold">Symbol</th>
                <th className="px-4 py-3 font-bold">Side</th>
                <th className="px-4 py-3 font-bold">Status</th>
                <th className="px-4 py-3 font-bold">Execution</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
              {!approvals?.length ? (
                <tr>
                  <td colSpan="5" className="px-4 py-4 text-center text-[var(--color-muted)]">No approval history found.</td>
                </tr>
              ) : (
                approvals.slice(0, 20).map((app, idx) => (
                  <tr key={idx} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                    <td className="px-4 py-3 font-mono tabular-nums text-xs text-[var(--color-muted)]">{new Date(app.created_at).toLocaleString()}</td>
                    <td className="px-4 py-3 font-bold">{app.symbol}</td>
                    <td className={`px-4 py-3 font-bold ${app.side === 'BUY' ? 'text-[var(--color-ok)]' : 'text-[var(--color-warn)]'}`}>{app.side}</td>
                    <td className="px-4 py-3 font-mono">{app.approval_status}</td>
                    <td className="px-4 py-3 font-mono">{app.execution_status || '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Execution Audit</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm overflow-x-auto max-h-96">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider sticky top-0">
              <tr>
                <th className="px-4 py-3 font-bold">Time</th>
                <th className="px-4 py-3 font-bold">Event Type</th>
                <th className="px-4 py-3 font-bold">Queue ID</th>
                <th className="px-4 py-3 font-bold">Status / Msg</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
              {!execution?.recent_execution_events?.length ? (
                <tr>
                  <td colSpan="4" className="px-4 py-4 text-center text-[var(--color-muted)]">No execution audit events found.</td>
                </tr>
              ) : (
                execution.recent_execution_events.map((evt, idx) => (
                  <tr key={idx} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                    <td className="px-4 py-3 font-mono tabular-nums text-xs text-[var(--color-muted)]">{new Date(evt.created_at).toLocaleString()}</td>
                    <td className="px-4 py-3 font-mono">{evt.event_type}</td>
                    <td className="px-4 py-3 font-mono tabular-nums">{evt.queue_id || '-'}</td>
                    <td className="px-4 py-3 text-xs truncate max-w-xs">{evt.status || evt.message || '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Audit History (Global)</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm overflow-x-auto max-h-96">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider sticky top-0">
              <tr>
                <th className="px-4 py-3 font-bold">Time</th>
                <th className="px-4 py-3 font-bold">Event Type</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
              {!auditEvents?.length ? (
                <tr>
                  <td colSpan="2" className="px-4 py-4 text-center text-[var(--color-muted)]">No audit events found.</td>
                </tr>
              ) : (
                auditEvents.slice(0, 50).map((evt, idx) => (
                  <tr key={idx} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                    <td className="px-4 py-3 font-mono tabular-nums text-xs text-[var(--color-muted)]">{new Date(evt.created_at).toLocaleString()}</td>
                    <td className="px-4 py-3 font-mono text-[var(--color-accent)]">{evt.event_type}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
