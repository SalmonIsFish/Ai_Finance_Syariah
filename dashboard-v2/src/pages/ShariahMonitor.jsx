import { useState } from "react";
import { RefreshCw, Upload, Plus } from "lucide-react";
import {
  addHolding,
  escalateHolding,
  fetchMonitorAlerts,
  fetchMonitorSettings,
  importHoldingsCsv,
  recordSale,
  removeHolding,
  reviewFiling,
  runMonitor,
  saveMonitorSettings,
} from "../api";
import DataTable from "../components/DataTable";
import ErrorNote from "../components/ErrorNote";
import Panel from "../components/Panel";
import Segmented from "../components/Segmented";
import useResource from "../useResource";
import { lightRank, lightStyle, primaryReason } from "../light";
import { fmtDate, fmtDateTime, fmtMoney, fmtQty, MISSING } from "../format";

const BUTTON =
  "px-3 py-1.5 rounded text-sm font-medium border border-[var(--color-border-strong)] text-[var(--color-text)] hover:bg-[var(--color-bg)] disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]";
const PRIMARY =
  "px-4 py-2 rounded text-sm font-medium bg-[var(--color-accent)] text-[var(--color-button-text)] hover:bg-[var(--color-accent-2)] disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]";
const INPUT =
  "px-2 py-1.5 rounded text-sm bg-[var(--color-bg)] border border-[var(--color-border-strong)] text-[var(--color-text)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]";

const METHODOLOGIES = [
  ["SC_MY_SAC", "SC Malaysia (default)"],
  ["AAOIFI", "AAOIFI"],
];

function LightBadge({ light }) {
  const style = lightStyle(light);
  return (
    <span className={`inline-block px-2 py-0.5 rounded border text-xs font-bold ${style.badge}`}>
      {style.label} · {style.meaning}
    </span>
  );
}

function Section({ title, children, actions = null }) {
  return (
    <Panel>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-lg font-semibold">{title}</h2>
        {actions}
      </div>
      {children}
    </Panel>
  );
}

/** The result of one action, shown where the action was taken. */
function Outcome({ outcome }) {
  if (!outcome) return null;
  const bad = outcome.error;
  return (
    <p
      role="status"
      className={`mt-2 text-sm ${bad ? "text-[var(--color-bad)]" : "text-[var(--color-muted)]"}`}
    >
      {bad ? outcome.error : outcome.message}
    </p>
  );
}

function useAction(after) {
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState(null);
  const run = async (fn, describe) => {
    setBusy(true);
    setOutcome(null);
    try {
      const result = await fn();
      setOutcome({ message: describe(result) });
      if (after) await after();
    } catch (err) {
      setOutcome({ error: err?.message || "Something went wrong." });
    } finally {
      setBusy(false);
    }
  };
  return { busy, outcome, run };
}

function HoldingDetail({ holding, onChanged }) {
  const light = holding.traffic_light?.light;
  const reasons = holding.traffic_light?.reasons || [];
  const [saleQty, setSaleQty] = useState("");
  const [salePrice, setSalePrice] = useState("");
  const [escalation, setEscalation] = useState("");
  const { busy, outcome, run } = useAction(onChanged);
  const isUS = !/^\d+$/.test(holding.symbol);

  return (
    <div className="space-y-4 text-sm">
      <p className="font-medium">{holding.advice}</p>

      <div>
        <h3 className="font-semibold mb-1">Why this colour</h3>
        {reasons.length === 0 ? (
          <p className="text-[var(--color-muted)]">{holding.reason || "No concerns found on the latest report."}</p>
        ) : (
          <ul className="list-disc pl-5 space-y-1">
            {reasons.map((r, i) => (
              <li key={`${r.code}-${i}`}>
                {r.detail || r.code}
                {r.source ? <span className="text-[var(--color-muted)]"> ({r.source})</span> : null}
                {r.quote ? (
                  <blockquote className="mt-1 pl-3 border-l-2 border-[var(--color-border-strong)] text-[var(--color-muted)] italic">
                    “{r.quote}”
                  </blockquote>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-[var(--color-muted)]">
          Screen: {holding.reason || MISSING} · Annual report: {fmtDate(holding.report_date)} · Source:{" "}
          {holding.provider || MISSING}
        </p>
        {light === "RED" && holding.disposal_deadline ? (
          <p className="mt-1">
            Disposal window ends <strong>{fmtDate(holding.disposal_deadline)}</strong>
            {holding.days_remaining !== null && holding.days_remaining !== undefined
              ? ` (${holding.days_remaining} days)`
              : ""}
            .
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-2">
        {isUS ? (
          <button
            type="button"
            className={BUTTON}
            disabled={busy}
            onClick={() =>
              run(
                () => reviewFiling(holding.symbol),
                (r) =>
                  r.status === "OK"
                    ? `Read the ${r.filing?.form || "annual report"}: ${r.verified_findings.length} quoted finding(s), ${r.rejected_findings.length} discarded. ${r.effect}.`
                    : `Not reviewed: ${r.reason || r.status}`,
              )
            }
          >
            Have AI read the annual report
          </button>
        ) : null}
        <button
          type="button"
          className={BUTTON}
          disabled={busy}
          onClick={() => {
            if (!window.confirm(`Remove ${holding.symbol} from the monitor? Use "Record sale" if you sold it.`)) return;
            run(() => removeHolding(holding.symbol, holding.broker), () => `${holding.symbol} removed.`);
          }}
        >
          Remove (entered by mistake)
        </button>
      </div>

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          run(
            () => recordSale({ symbol: holding.symbol, broker: holding.broker, quantity: saleQty, price: salePrice }),
            (r) =>
              r.warning ||
              (r.purification_due > 0
                ? `Recorded. ${fmtMoney(r.purification_due, "US")} of the gain is owed to baitulmal.`
                : "Recorded. Nothing is owed on this sale."),
          );
        }}
      >
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-muted)]">Sold quantity</span>
          <input className={`${INPUT} w-28`} inputMode="decimal" value={saleQty} onChange={(e) => setSaleQty(e.target.value)} required />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-muted)]">Sale price</span>
          <input className={`${INPUT} w-28`} inputMode="decimal" value={salePrice} onChange={(e) => setSalePrice(e.target.value)} required />
        </label>
        <button type="submit" className={BUTTON} disabled={busy}>
          Record sale
        </button>
      </form>
      {light === "YELLOW" ? (
        <p className="text-xs text-[var(--color-warn)]">
          This holding is under review. Your position is not to sell yet; if you do and it later turns red, the
          gain above cost on that sale is owed to baitulmal.
        </p>
      ) : null}

      {light !== "RED" ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            run(
              () => escalateHolding(holding.symbol, escalation, holding.report_date),
              () => `${holding.symbol} marked red until a newer annual report.`,
            );
          }}
        >
          <label className="flex flex-col gap-1 grow">
            <span className="text-xs text-[var(--color-muted)]">
              Mark as red — why (there is no way to mark green by hand)
            </span>
            <input className={INPUT} value={escalation} onChange={(e) => setEscalation(e.target.value)} required />
          </label>
          <button type="submit" className={BUTTON} disabled={busy}>
            Mark red
          </button>
        </form>
      ) : null}
      <Outcome outcome={outcome} />
    </div>
  );
}

const COLUMNS = [
  { key: "symbol", label: "Symbol" },
  { key: "broker", label: "Broker" },
  {
    key: "light",
    label: "Status",
    value: (h) => lightRank(h.traffic_light?.light),
    render: (h) => <LightBadge light={h.traffic_light?.light} />,
  },
  {
    key: "why",
    label: "Why",
    sortable: false,
    value: (h) => primaryReason(h.traffic_light),
    render: (h) => (
      <span className="text-sm text-[var(--color-muted)]">{primaryReason(h.traffic_light) || "—"}</span>
    ),
  },
  { key: "quantity", label: "Qty", numeric: true, render: (h) => fmtQty(h.quantity) },
  {
    key: "average_cost",
    label: "Avg cost",
    numeric: true,
    render: (h) => fmtMoney(h.average_cost, h.market),
  },
];

function AddHolding({ onChanged }) {
  const [form, setForm] = useState({ symbol: "", quantity: "", average_cost: "", broker: "manual" });
  const { busy, outcome, run } = useAction(onChanged);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        run(
          () => addHolding(form),
          (r) => {
            setForm({ ...form, symbol: "", quantity: "", average_cost: "" });
            return `${r.symbol} saved.`;
          },
        );
      }}
    >
      {[
        ["symbol", "Symbol (e.g. AAPL)", "w-32"],
        ["quantity", "Quantity", "w-28"],
        ["average_cost", "Average buy price", "w-36"],
        ["broker", "Broker", "w-28"],
      ].map(([key, label, width]) => (
        <label key={key} className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-muted)]">{label}</span>
          <input className={`${INPUT} ${width}`} value={form[key]} onChange={set(key)} required />
        </label>
      ))}
      <button type="submit" className={PRIMARY} disabled={busy}>
        <Plus size={14} className="inline mr-1" aria-hidden="true" />
        Add holding
      </button>
      <div className="basis-full">
        <Outcome outcome={outcome} />
      </div>
    </form>
  );
}

function ImportCsv({ onChanged }) {
  const [broker, setBroker] = useState("moomoo");
  const { busy, outcome, run } = useAction(onChanged);
  const onFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    run(
      async () => importHoldingsCsv(broker, await file.text()),
      (r) => `Imported ${r.imported} holding(s) for ${r.broker}. Previous ${r.broker} holdings were replaced.`,
    );
  };
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex flex-col gap-1">
        <span className="text-xs text-[var(--color-muted)]">Broker</span>
        <input className={`${INPUT} w-32`} value={broker} onChange={(e) => setBroker(e.target.value)} />
      </label>
      <label className={`${BUTTON} cursor-pointer`}>
        <Upload size={14} className="inline mr-1" aria-hidden="true" />
        Choose positions CSV
        <input type="file" accept=".csv,text/csv" className="sr-only" onChange={onFile} disabled={busy} />
      </label>
      <p className="basis-full text-xs text-[var(--color-muted)]">
        Needs columns for symbol, quantity and average cost (or total cost). If any row is unreadable, nothing is
        imported.
      </p>
      <div className="basis-full">
        <Outcome outcome={outcome} />
      </div>
    </div>
  );
}

function Purification({ monitor }) {
  const estimate = monitor.purification || {};
  const sales = monitor.sales || {};
  return (
    <div className="grid gap-4 sm:grid-cols-2 text-sm">
      <div>
        <h3 className="font-semibold mb-1">If you sold your red holdings today</h3>
        <p className="text-2xl font-semibold tabular-nums">
          {/* With a holding unpriced the total is a floor, not the amount owed. */}
          {estimate.unpriced?.length ? "At least " : ""}
          {fmtMoney(estimate.total_purification_due, "US")}
        </p>
        <p className="text-[var(--color-muted)]">
          Estimate of the gain above cost owed to baitulmal.
          {estimate.unpriced?.length ? ` No price for: ${estimate.unpriced.join(", ")} — not included.` : ""}
        </p>
      </div>
      <div>
        <h3 className="font-semibold mb-1">Owed on sales you recorded</h3>
        <p className="text-2xl font-semibold tabular-nums">{fmtMoney(sales.total_owed_on_sales, "US")}</p>
        <p className="text-[var(--color-muted)]">
          {sales.pending?.length
            ? `${sales.pending.length} sale(s) made while under review are waiting for a green or red result.`
            : "No sales are waiting on a review."}
        </p>
      </div>
      <p className="sm:col-span-2 text-xs text-[var(--color-muted)]">
        Pay through your state zakat or baitulmal body. This app calculates the amount; it never holds money.
      </p>
    </div>
  );
}

export default function ShariahMonitor() {
  const monitor = useResource(runMonitor);
  const alerts = useResource(fetchMonitorAlerts);
  const settings = useResource(fetchMonitorSettings);
  const [savingMethod, setSavingMethod] = useState(false);

  const refreshAll = async () => {
    await monitor.refresh();
    await alerts.refresh();
  };

  const counts = monitor.data?.counts || {};
  const holdings = monitor.data?.holdings || [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Shariah Monitor</h1>
          <p className="text-sm text-[var(--color-muted)] max-w-2xl">
            Your holdings, checked against the latest annual report. Green: compliant. Yellow: under review — don’t
            buy or sell yet. Red: not compliant — plan to sell and purify the gain.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Segmented
            label="Screening standard"
            options={METHODOLOGIES}
            value={settings.data?.methodology || "SC_MY_SAC"}
            onChange={async (value) => {
              setSavingMethod(true);
              try {
                await saveMonitorSettings(value);
                await settings.refresh();
                await refreshAll();
              } finally {
                setSavingMethod(false);
              }
            }}
          />
          <button type="button" className={BUTTON} onClick={refreshAll} disabled={monitor.loading || savingMethod}>
            <RefreshCw size={14} className="inline mr-1" aria-hidden="true" />
            Re-check now
          </button>
        </div>
      </div>

      {monitor.error ? <ErrorNote what="the monitor" error={monitor.error} /> : null}

      <div className="grid grid-cols-3 gap-3">
        {["GREEN", "YELLOW", "RED"].map((light) => (
          <Panel key={light} className="text-center">
            <p className="text-3xl font-semibold tabular-nums">{monitor.data ? (counts[light] ?? 0) : MISSING}</p>
            <LightBadge light={light} />
          </Panel>
        ))}
      </div>

      <Section
        title="Holdings"
        actions={
          monitor.asOf ? (
            <span className="text-xs text-[var(--color-muted)]">Checked {fmtDateTime(monitor.asOf)}</span>
          ) : null
        }
      >
        {monitor.loading && !monitor.data ? (
          <p className="text-sm text-[var(--color-muted)]">Checking each holding against SEC filings…</p>
        ) : (
          <DataTable
            caption="Monitored holdings"
            rows={holdings}
            rowKey={(h) => `${h.symbol}-${h.broker}`}
            columns={COLUMNS}
            defaultSort={{ key: "light", dir: "asc" }}
            expand={(row) => <HoldingDetail holding={row} onChanged={refreshAll} />}
            empty="No holdings yet. Add one below or import your broker's positions CSV."
          />
        )}
        {monitor.data?.basis ? <p className="mt-3 text-xs text-[var(--color-muted)]">{monitor.data.basis}</p> : null}
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Add a holding">
          <AddHolding onChanged={refreshAll} />
        </Section>
        <Section title="Import from your broker">
          <ImportCsv onChanged={refreshAll} />
        </Section>
      </div>

      <Section title="Purification">
        {monitor.data ? <Purification monitor={monitor.data} /> : <p className="text-sm">{MISSING}</p>}
      </Section>

      <Section title="Recent colour changes">
        {alerts.error ? <ErrorNote what="alerts" error={alerts.error} /> : null}
        {(alerts.data?.alerts || []).length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No colour changes yet.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {alerts.data.alerts.map((a) => (
              <li key={a.id} className="flex flex-wrap gap-2 items-baseline">
                <span className="text-xs text-[var(--color-muted)] tabular-nums">{fmtDateTime(a.created_at)}</span>
                <LightBadge light={a.to_light} />
                <span>{a.message}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}
