import { RefreshCw } from "lucide-react";
import useResource from "../useResource";
import { fetchPaperStatus, fetchAccount, fetchMarketClock } from "../api";
import { fmtTime, fmtDateTime, isPresent } from "../format";

const POLL_MS = 30_000;

// Operational state, not a verdict: a neutral chip with a coloured dot. The
// verdict palette means permitted/refused, and "the broker answered" is neither.
const DOT = {
  ok: "bg-[var(--color-ok)]",
  bad: "bg-[var(--color-bad)]",
  unknown: "bg-[var(--color-unknown)]",
  idle: "bg-[var(--color-muted)]",
};

function Chip({ tone, label, title }) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-[var(--color-border)] bg-[var(--color-panel)] text-xs text-[var(--color-text)] whitespace-nowrap"
    >
      <span className={`w-2 h-2 rounded-full shrink-0 ${DOT[tone] || DOT.unknown}`} aria-hidden="true" />
      {label}
    </span>
  );
}

/**
 * The always-visible answer to "what am I looking at, and how fresh is it?".
 *
 * Every item is read from the backend; nothing here is asserted by the client.
 * Where a source cannot be read the chip says "unknown", never a reassuring
 * default -- the same rule as check_market_clock, whose is_open is null rather
 * than false when the clock is unreachable.
 */
export default function StatusBar() {
  const status = useResource(fetchPaperStatus, { intervalMs: POLL_MS });
  const account = useResource(fetchAccount, { intervalMs: POLL_MS });
  const clock = useResource(fetchMarketClock, { intervalMs: POLL_MS });

  // Environment. live_trading is pinned false by the backend; anything else --
  // including not knowing -- must not render as the reassuring PAPER badge.
  const environment =
    status.data?.live_trading === false ? (
      <span
        className="px-2.5 py-1 rounded text-xs font-bold uppercase tracking-wider bg-[var(--color-warn-bg)] text-[var(--color-warn)] border border-[var(--color-warn)]"
        title="Paper trading only. Live trading is disabled in the backend configuration."
      >
        Paper Trading
      </span>
    ) : status.data?.live_trading === true ? (
      <span className="px-2.5 py-1 rounded text-xs font-bold uppercase tracking-wider bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)]">
        Live Trading
      </span>
    ) : (
      <Chip tone="unknown" label="Mode unknown" title={status.error?.message} />
    );

  // Broker: connected only when the account actually returned figures.
  const brokerChip = account.error ? (
    <Chip tone="bad" label="Broker unreachable" title={account.error.message} />
  ) : !account.data ? (
    <Chip tone="idle" label="Broker…" />
  ) : isPresent(account.data.equity) ? (
    <Chip tone="ok" label="Broker connected" title={account.data.account_suffix ? `Account …${account.data.account_suffix}` : undefined} />
  ) : (
    <Chip tone="bad" label={`Broker: ${account.data.status || "no data"}`} />
  );

  // US market: Alpaca's clock. Bursa hours are not reported rather than guessed.
  const isOpen = clock.data?.is_open;
  const clockChip = clock.error ? (
    <Chip tone="unknown" label="US market: unknown" title={clock.error.message} />
  ) : !clock.data ? (
    <Chip tone="idle" label="US market…" />
  ) : isOpen === true ? (
    <Chip tone="ok" label="US market open" title={clock.data.next_close ? `Closes ${fmtDateTime(clock.data.next_close)}` : undefined} />
  ) : isOpen === false ? (
    <Chip tone="idle" label="US market closed" title={clock.data.next_open ? `Opens ${fmtDateTime(clock.data.next_open)}` : undefined} />
  ) : (
    <Chip tone="unknown" label="US market: unknown" title={clock.data.status} />
  );

  // Execution, per market, as /paper/status reports it.
  const markets = status.data?.execution_markets;
  const executing = markets
    ? Object.entries(markets).filter(([, m]) => m?.enabled).map(([code]) => code)
    : null;
  const executionChip =
    executing === null ? null : executing.length ? (
      <Chip tone="ok" label={`Execution: ${executing.join(", ")}`} title="Markets this instance will submit orders for (via the operator relay)" />
    ) : (
      <Chip tone="idle" label="Execution off" />
    );

  // Freshness: the oldest of the three, and whether any refresh is overdue.
  const times = [status.asOf, account.asOf, clock.asOf].filter(Boolean);
  const oldest = times.length ? new Date(Math.min(...times.map((t) => t.getTime()))) : null;
  const stale = status.stale || account.stale || clock.stale;
  const refreshAll = () => {
    status.refresh();
    account.refresh();
    clock.refresh();
  };

  return (
    <div className="flex items-center gap-2 flex-wrap min-w-0" aria-live="polite">
      {environment}
      {brokerChip}
      {clockChip}
      {executionChip}
      <span
        className={`text-xs whitespace-nowrap ${stale ? "text-[var(--color-warn)] font-bold" : "text-[var(--color-muted)]"}`}
        title={stale ? "Refresh overdue — the last updates failed" : undefined}
      >
        {oldest ? `${stale ? "Stale · " : ""}Updated ${fmtTime(oldest)}` : "Not updated yet"}
      </span>
      <button
        type="button"
        onClick={refreshAll}
        aria-label="Refresh status"
        className="p-1.5 rounded-md text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      >
        <RefreshCw className="w-4 h-4" aria-hidden="true" />
      </button>
    </div>
  );
}
