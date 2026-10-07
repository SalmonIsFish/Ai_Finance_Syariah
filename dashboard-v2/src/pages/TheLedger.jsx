import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { fetchApprovals, fetchExecutionAudit, fetchAuditEvents } from "../api";
import ErrorNote from "../components/ErrorNote";
import DataTable from "../components/DataTable";
import Segmented from "../components/Segmented";
import useResource from "../useResource";
import { verdictBadgeClass } from "../verdict";
import { fmtDateTime, fmtMoney, fmtQty, fmtTime } from "../format";

const PAGE_SIZE = 25;

/** Payloads are stored as JSON text. Show them parsed and indented; never guess. */
function PrettyPayload({ payload }) {
  if (payload === null || payload === undefined || payload === "") {
    return <span className="text-[var(--color-muted)] text-xs">No payload recorded.</span>;
  }
  let text = payload;
  if (typeof payload === "string") {
    try {
      text = JSON.stringify(JSON.parse(payload), null, 2);
    } catch {
      text = payload;
    }
  } else {
    text = JSON.stringify(payload, null, 2);
  }
  return (
    <pre className="text-xs font-mono text-[var(--color-text)] bg-[var(--color-panel)] border border-[var(--color-border)] rounded p-3 max-h-80 overflow-auto whitespace-pre-wrap break-words">
      {text}
    </pre>
  );
}

/** The approval verdict is a gate outcome, so it uses the verdict palette. */
function approvalVerdict(status) {
  if (status === "APPROVED_PAPER_READY") return "PASS";
  if (status === "REJECT") return "REJECT";
  return "UNKNOWN";
}

/** Broker outcomes: filled is done, submitted is in flight, a refusal is a refusal. */
function executionVerdict(status) {
  if (!status || status === "NOT_EXECUTED") return null;
  if (status.includes("FILLED")) return "PASS";
  if (status.includes("SUBMITTED") || status.includes("ACCEPTED") || status.includes("NEW")) return "WARN";
  if (/FAILED|REJECT|LOCKED|BLOCKED|NOT_READY|NOT_CONFIGURED|REQUIRED|ERROR/.test(status)) return "REJECT";
  return "UNKNOWN";
}

function StatusChip({ verdict, label }) {
  if (!label) return <span className="text-[var(--color-muted)]">—</span>;
  return (
    <span className={`px-2 py-0.5 rounded text-[11px] font-bold font-mono ${verdictBadgeClass(verdict)}`}>{label}</span>
  );
}

function SearchBox({ value, onChange, placeholder, label }) {
  return (
    <label className="relative block">
      <span className="sr-only">{label}</span>
      <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-muted)]" aria-hidden="true" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        className="pl-8 pr-3 py-1.5 w-56 bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      />
    </label>
  );
}

function Section({ title, resource, children, controls }) {
  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-3 mb-3">
        <h2 className="text-lg font-bold text-[var(--color-text)]">
          {title}
          {resource.asOf ? (
            <span className="text-xs font-normal ml-3 text-[var(--color-muted)]">as of {fmtTime(resource.asOf)}</span>
          ) : null}
        </h2>
        <div className="flex flex-wrap items-center gap-2">{controls}</div>
      </div>
      <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm">{children}</div>
    </section>
  );
}

const APPROVAL_FILTERS = [
  ["ALL", "All"],
  ["APPROVED", "Approved"],
  ["REFUSED", "Refused"],
  ["EXECUTED", "Sent to broker"],
];

const APPROVAL_COLUMNS = [
  { key: "id", label: "#", numeric: true },
  { key: "created_at", label: "Time", render: (a) => <span className="text-xs text-[var(--color-muted)] font-mono">{fmtDateTime(a.created_at)}</span> },
  { key: "symbol", label: "Symbol", className: "font-bold" },
  { key: "side", label: "Side", className: "font-mono" },
  { key: "quantity", label: "Qty", numeric: true, render: (a) => fmtQty(a.quantity) },
  { key: "price", label: "Price", numeric: true, group: (a) => a.shariah_market, render: (a) => fmtMoney(a.price, a.shariah_market) },
  { key: "notional", label: "Notional", numeric: true, group: (a) => a.shariah_market, render: (a) => fmtMoney(a.notional, a.shariah_market) },
  {
    key: "approval_status",
    label: "Approval",
    render: (a) => <StatusChip verdict={approvalVerdict(a.approval_status)} label={a.approval_status} />,
  },
  {
    key: "execution_status",
    label: "Execution",
    render: (a) => <StatusChip verdict={executionVerdict(a.execution_status)} label={a.execution_status && a.execution_status !== "NOT_EXECUTED" ? a.execution_status : null} />,
  },
];

function ApprovalDetail({ row }) {
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        {[
          ["Market", row.shariah_market],
          ["Shariah (security)", row.shariah_status],
          ["Quant signal", row.quant_signal],
          ["Risk", row.risk_status],
          ["Environment", row.execution_environment],
          ["Sent to broker", row.broker_submission ? "yes" : "no"],
          ["Executed at", row.executed_at ? fmtDateTime(row.executed_at) : null],
          ["Execution message", row.execution_message],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-[var(--color-muted)] uppercase tracking-wider text-[10px] font-bold">{label}</dt>
            <dd className="font-mono text-[var(--color-text)] break-words">{value ?? "—"}</dd>
          </div>
        ))}
      </dl>
      <div>
        <div className="text-[10px] uppercase tracking-wider font-bold text-[var(--color-muted)] mb-1">Recorded payload</div>
        <PrettyPayload payload={row.payload} />
      </div>
    </div>
  );
}

function matchesApprovalFilter(row, filter) {
  if (filter === "APPROVED") return row.approval_status === "APPROVED_PAPER_READY";
  if (filter === "REFUSED") return row.approval_status === "REJECT";
  if (filter === "EXECUTED") return Boolean(row.broker_submission);
  return true;
}

const EXECUTION_COLUMNS = [
  { key: "created_at", label: "Time", render: (e) => <span className="text-xs text-[var(--color-muted)] font-mono">{fmtDateTime(e.created_at)}</span> },
  { key: "event_type", label: "Event", className: "font-mono" },
  { key: "queue_id", label: "Queue #", numeric: true },
  {
    key: "status",
    label: "Status / Message",
    value: (e) => e.status || e.message,
    render: (e) => (
      <span className="text-xs whitespace-normal">
        {e.status ? <StatusChip verdict={executionVerdict(e.status)} label={e.status} /> : null}
        {e.message ? <span className="block mt-1 text-[var(--color-muted)]">{e.message}</span> : null}
        {!e.status && !e.message ? "—" : null}
      </span>
    ),
  },
];

const AUDIT_COLUMNS = [
  { key: "id", label: "#", numeric: true },
  { key: "created_at", label: "Time", render: (e) => <span className="text-xs text-[var(--color-muted)] font-mono">{fmtDateTime(e.created_at)}</span> },
  { key: "event_type", label: "Event", className: "font-mono text-[var(--color-accent)]" },
];

export default function TheLedger() {
  const approvals = useResource(fetchApprovals);
  const execution = useResource(fetchExecutionAudit);
  const audit = useResource(fetchAuditEvents);

  const [approvalQuery, setApprovalQuery] = useState("");
  const [approvalFilter, setApprovalFilter] = useState("ALL");
  const [auditQuery, setAuditQuery] = useState("");

  const approvalRows = useMemo(() => {
    const q = approvalQuery.trim().toUpperCase();
    return (approvals.data || []).filter(
      (row) =>
        matchesApprovalFilter(row, approvalFilter) &&
        (!q || String(row.symbol || "").toUpperCase().includes(q) || String(row.id) === q.replace(/^#/, "")),
    );
  }, [approvals.data, approvalQuery, approvalFilter]);

  const auditRows = useMemo(() => {
    const q = auditQuery.trim().toLowerCase();
    return (audit.data || []).filter((row) => !q || String(row.event_type || "").toLowerCase().includes(q));
  }, [audit.data, auditQuery]);

  const executionRows = execution.data?.recent_execution_events || [];

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">The Ledger</h1>
      </div>

      {/* A table that failed to load says so. An empty table reads as "nothing
          has happened" -- the one claim an audit ledger must never make by
          accident. */}
      <Section
        title="Approval Queue"
        resource={approvals}
        controls={
          <>
            <SearchBox value={approvalQuery} onChange={setApprovalQuery} placeholder="Symbol or #id…" label="Search approvals" />
            <Segmented options={APPROVAL_FILTERS} value={approvalFilter} onChange={setApprovalFilter} label="Filter approvals" />
          </>
        }
      >
        {approvals.loading ? (
          <div className="px-4 py-4 text-sm text-[var(--color-muted)]">Loading…</div>
        ) : approvals.error ? (
          <div className="p-4"><ErrorNote what="the approval queue" error={approvals.error} /></div>
        ) : (
          <DataTable
            caption="Approval queue"
            rows={approvalRows}
            rowKey={(a) => a.id}
            columns={APPROVAL_COLUMNS}
            defaultSort={{ key: "id", dir: "desc" }}
            expand={(row) => <ApprovalDetail row={row} />}
            pageSize={PAGE_SIZE}
            empty={approvals.data?.length ? "No approvals match." : "No approval history found."}
          />
        )}
      </Section>

      <Section title="Execution Audit" resource={execution}>
        {execution.loading ? (
          <div className="px-4 py-4 text-sm text-[var(--color-muted)]">Loading…</div>
        ) : execution.error ? (
          <div className="p-4"><ErrorNote what="the execution audit" error={execution.error} /></div>
        ) : (
          <DataTable
            caption="Execution audit"
            rows={executionRows}
            rowKey={(e) => e.id ?? `${e.created_at}-${e.event_type}-${e.queue_id}`}
            columns={EXECUTION_COLUMNS}
            defaultSort={{ key: "created_at", dir: "desc" }}
            expand={(row) => <PrettyPayload payload={row} />}
            pageSize={PAGE_SIZE}
            empty="No execution audit events found."
          />
        )}
      </Section>

      <Section
        title="Audit History"
        resource={audit}
        controls={<SearchBox value={auditQuery} onChange={setAuditQuery} placeholder="Event type…" label="Search audit events" />}
      >
        {audit.loading ? (
          <div className="px-4 py-4 text-sm text-[var(--color-muted)]">Loading…</div>
        ) : audit.error ? (
          <div className="p-4"><ErrorNote what="the audit history" error={audit.error} /></div>
        ) : (
          <DataTable
            caption="Audit history"
            rows={auditRows}
            rowKey={(e) => e.id}
            columns={AUDIT_COLUMNS}
            defaultSort={{ key: "id", dir: "desc" }}
            expand={(row) => <PrettyPayload payload={row.payload} />}
            pageSize={PAGE_SIZE}
            empty={audit.data?.length ? "No events match." : "No audit events found."}
          />
        )}
        <p className="px-4 py-2 text-xs text-[var(--color-muted)] border-t border-[var(--color-border)]">
          Shows the latest 100 events the server returns.
        </p>
      </Section>
    </div>
  );
}
