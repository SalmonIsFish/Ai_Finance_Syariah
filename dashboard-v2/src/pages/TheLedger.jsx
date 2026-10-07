import { useState, useEffect } from "react";
import { fetchApprovals, fetchExecutionAudit, fetchAuditEvents } from "../api";
import ErrorNote from "../components/ErrorNote";

export default function TheLedger() {
  const [data, setData] = useState({ approvals: null, execution: null, auditEvents: null });
  const [errors, setErrors] = useState({});
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

        // A table that failed to load says so. These used to go to console.error
        // only, and an empty table reads as "nothing has happened" -- the one
        // claim an audit ledger must never make by accident.
        const value = (r) => (r.status === "fulfilled" ? r.value : null);
        const failure = (r) => (r.status === "rejected" ? r.reason : null);
        setData({
          approvals: value(approvalsResult),
          execution: value(executionResult),
          auditEvents: value(auditEventsResult),
        });
        setErrors({
          approvals: failure(approvalsResult),
          execution: failure(executionResult),
          auditEvents: failure(auditEventsResult),
        });
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <div className="text-[var(--color-muted)] animate-pulse">Loading The Ledger…</div>;
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
                <th className="px-4 py-3 font-bold">#</th>
                <th className="px-4 py-3 font-bold">Time</th>
                <th className="px-4 py-3 font-bold">Symbol</th>
                <th className="px-4 py-3 font-bold">Side</th>
                <th className="px-4 py-3 font-bold">Status</th>
                <th className="px-4 py-3 font-bold">Execution</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
              {errors.approvals ? (
                <tr>
                  <td colSpan="6" className="px-4 py-4"><ErrorNote what="the approval queue" error={errors.approvals} /></td>
                </tr>
              ) : !approvals?.length ? (
                <tr>
                  <td colSpan="6" className="px-4 py-4 text-center text-[var(--color-muted)]">No approval history found.</td>
                </tr>
              ) : (
                // The queue id is what The Desk reports after an approval and what
                // execution is addressed by, so it is the row's identity here too.
                approvals.slice(0, 20).map((app, idx) => (
                  <tr key={app.id ?? idx} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                    <td className="px-4 py-3 font-mono tabular-nums text-xs">{app.id ?? '—'}</td>
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
              {errors.execution ? (
                <tr>
                  <td colSpan="4" className="px-4 py-4"><ErrorNote what="the execution audit" error={errors.execution} /></td>
                </tr>
              ) : !execution?.recent_execution_events?.length ? (
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
              {errors.auditEvents ? (
                <tr>
                  <td colSpan="2" className="px-4 py-4"><ErrorNote what="the audit history" error={errors.auditEvents} /></td>
                </tr>
              ) : !auditEvents?.length ? (
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
