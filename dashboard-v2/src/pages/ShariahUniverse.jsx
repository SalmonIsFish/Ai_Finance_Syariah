/**
 * What is eligible at all -- as distinct from what is worth trading today.
 *
 * The two markets are shown in separate tabs rather than one merged list,
 * because they are not the same kind of thing and merging them would imply a
 * parity that does not exist:
 *
 *   Malaysia  an authority's published list. The SC's Shariah Advisory Council
 *             classifies the securities; this system applies that list and can
 *             prove which document it applied, by hash. Complete by definition.
 *
 *   US        no such list exists. Verdicts come from a self-built ratio screen
 *             over SEC EDGAR filings which sec_edgar_screen.py's own docstring
 *             calls "not a certified screening service". This tab can only ever
 *             show what has actually been screened, which is a fact about our
 *             activity, not about the market.
 *
 * Absence means different things on each tab, and the copy says so on both.
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search, ArrowRight } from "lucide-react";
import { fetchUniverse, fetchPublication, fetchScreenedUS } from "../api";
import { verdictBadgeClass } from "../verdict";
import { normalizeVerdict, verdictLabel } from "../shariah";
import { fmtPct, fmtDate, fmtDateTime } from "../format";
import DataTable from "../components/DataTable";
import Segmented from "../components/Segmented";

/** The SC list is ~900 rows; nobody reads them all. Search narrows, paging
 *  bounds what is drawn, and the pager always states the full filtered count,
 *  so a page can never read as "that is all there is". */
const PAGE_SIZE = 50;

const TAB_CLASS_ACTIVE =
  "px-4 py-2 text-sm font-bold border-b-2 border-[var(--color-accent)] text-[var(--color-text)]";
const TAB_CLASS_IDLE =
  "px-4 py-2 text-sm font-bold border-b-2 border-transparent text-[var(--color-muted)] hover:text-[var(--color-text)] transition-colors";

function VerdictChip({ status }) {
  const verdict = normalizeVerdict(status);
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-bold ${verdictBadgeClass(verdict)}`}>
      {verdictLabel(status)}
    </span>
  );
}

/** Opens a pre-filled ticket. It buys nothing: the order still has to clear
 *  every gate at /paper/preview, be approved, and be executed with the
 *  confirmation phrase. */
function TicketLink({ symbol }) {
  return (
    <Link
      to={`/?symbol=${encodeURIComponent(symbol)}`}
      className="inline-flex items-center gap-1 text-xs font-bold text-[var(--color-accent)] hover:underline focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] rounded"
    >
      Open <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
    </Link>
  );
}

const MY_COLUMNS = [
  { key: "ticker", label: "Ticker", className: "font-mono font-bold" },
  { key: "issuer_name", label: "Company" },
  { key: "board", label: "Board", className: "text-[var(--color-muted)]" },
  { key: "sector", label: "Sector", className: "text-[var(--color-muted)]" },
  { key: "verdict", label: "Status", value: (s) => normalizeVerdict(s.verdict), render: (s) => <VerdictChip status={s.verdict} /> },
  { key: "ticket", label: "Ticket", sortable: false, align: "right", render: (s) => <TicketLink symbol={s.ticker} /> },
];

const US_COLUMNS = [
  { key: "symbol", label: "Symbol", className: "font-mono font-bold" },
  { key: "status", label: "Status", value: (r) => normalizeVerdict(r.status), render: (r) => <VerdictChip status={r.status} /> },
  { key: "debt_ratio_pct", label: "Debt", numeric: true, title: "Debt / total assets; limit 33%", render: (r) => fmtPct(r.debt_ratio_pct, 1) },
  { key: "cash_ratio_pct", label: "Cash", numeric: true, title: "Interest-bearing cash / total assets; limit 33%", render: (r) => fmtPct(r.cash_ratio_pct, 1) },
  { key: "report_date", label: "Filing", render: (r) => <span className="text-[var(--color-muted)]">{fmtDate(r.report_date)}</span> },
  { key: "screened_at", label: "Screened", render: (r) => <span className="text-[var(--color-muted)]">{fmtDate(r.screened_at)}</span> },
  { key: "ticket", label: "Ticket", sortable: false, align: "right", render: (r) => <TicketLink symbol={r.symbol} /> },
];

function MalaysiaTab() {
  const [universe, setUniverse] = useState(null);
  const [publication, setPublication] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchUniverse("ALL");
        if (cancelled) return;
        setUniverse(data);
        // Provenance lives on the publication, not on the universe response.
        // A failure here must not blank the securities list, so it is caught
        // separately and simply leaves the footer out.
        if (data?.active_publication?.id) {
          try {
            const pub = await fetchPublication(data.active_publication.id);
            if (!cancelled) setPublication(pub);
          } catch {
            /* provenance is additive; the list still stands without it */
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Memoised because the `?? []` fallback would otherwise be a new array on
  // every render, making the filter and count memos below recompute each time
  // over ~900 rows.
  const securities = useMemo(() => universe?.securities ?? [], [universe]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return securities.filter((s) => {
      if (statusFilter !== "ALL" && normalizeVerdict(s.verdict) !== statusFilter) return false;
      if (!needle) return true;
      return (
        String(s.ticker ?? "").toLowerCase().includes(needle) ||
        String(s.issuer_name ?? "").toLowerCase().includes(needle)
      );
    });
  }, [securities, query, statusFilter]);

  const counts = useMemo(() => {
    let pass = 0;
    let reject = 0;
    for (const s of securities) {
      if (normalizeVerdict(s.verdict) === "PASS") pass += 1;
      else if (normalizeVerdict(s.verdict) === "REJECT") reject += 1;
    }
    return { pass, reject };
  }, [securities]);

  if (loading) {
    return <div className="text-[var(--color-muted)] animate-pulse">Loading the SC list…</div>;
  }
  if (error) {
    return <div className="text-[var(--color-bad)] p-4 bg-[var(--color-bad-bg)] rounded">{error}</div>;
  }

  // An empty table here would read as "nothing is compliant", which is a claim.
  // The truth is that no authority is loaded, which is a different statement.
  if (!universe?.active_publication) {
    return (
      <div className="p-4 rounded border border-[var(--color-unknown)] bg-[var(--color-unknown-bg)]">
        <p className="font-bold text-[var(--color-unknown)]">No SC publication is active.</p>
        <p className="text-sm text-[var(--color-muted)] mt-2">
          Malaysian screening is switched off: no list is loaded to check against, so every
          Bursa ticker returns UNKNOWN and trading is blocked. This is not a statement that
          any security is ineligible.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-muted)]" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search ticker or company…"
            aria-label="Search the SC list by ticker or company"
            className="w-full pl-9 pr-3 py-2 bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-accent)]"
          />
        </div>
        <Segmented
          label="Filter by classification"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            ["ALL", `All ${securities.length}`],
            ["PASS", `Compliant ${counts.pass}`],
            ["REJECT", `Not compliant ${counts.reject}`],
          ]}
        />
      </div>

      <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm">
        <DataTable
          caption="SC Malaysia Shariah list"
          rows={filtered}
          rowKey={(row) => row.id}
          columns={MY_COLUMNS}
          defaultSort={{ key: "ticker", dir: "asc" }}
          pageSize={PAGE_SIZE}
          empty="No security matches that search."
        />
      </div>

      {filtered.length !== securities.length ? (
        <p className="text-xs text-[var(--color-muted)]">
          {filtered.length} of {securities.length} securities match.
        </p>
      ) : null}

      {publication ? (
        <div className="p-4 rounded border border-[var(--color-border)] bg-[var(--color-panel-2)] text-xs text-[var(--color-muted)] space-y-1">
          <p className="font-bold text-[var(--color-subtle)] uppercase tracking-wider">
            Source document
          </p>
          <p>
            {publication.publication?.id} &middot; published{" "}
            {fmtDate(publication.publication?.publication_date)} &middot; {publication.compliant_count}{" "}
            compliant, {publication.non_compliant_count} not compliant
          </p>
          <p>
            Approved by {publication.publication?.approved_by}, activated by{" "}
            {publication.publication?.activated_by} at {fmtDateTime(publication.publication?.activated_at)}
          </p>
          {/* The hash is the point: it is what ties a verdict to the exact SC
              document it came from, rather than to our say-so. */}
          <p className="font-mono break-all">
            sha256 {publication.publication?.source_document_hash}
          </p>
          <p className="pt-2 text-[var(--color-subtle)]">
            Classification is the Securities Commission Malaysia&rsquo;s, not this
            system&rsquo;s. Being on this list settles the status of a security; it certifies
            neither a trading strategy nor this system.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function UnitedStatesTab() {
  const [screens, setScreens] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchScreenedUS()
      .then((data) => {
        if (!cancelled) setScreens(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <div className="text-[var(--color-muted)] animate-pulse">Loading screened symbols…</div>;
  }
  if (error) {
    return <div className="text-[var(--color-bad)] p-4 bg-[var(--color-bad-bg)] rounded">{error}</div>;
  }

  const rows = screens ?? [];

  return (
    <div className="space-y-4">
      {/* This callout is the whole reason the US tab is separate. Without it a
          reader would take a short list of American companies as the roster of
          compliant US stocks, which it is emphatically not. */}
      <div className="p-4 rounded border border-[var(--color-warn)] bg-[var(--color-warn-bg)]">
        <p className="font-bold text-[var(--color-warn)]">
          There is no official list of Shariah-compliant US securities.
        </p>
        <p className="text-sm text-[var(--color-muted)] mt-2">
          Malaysia has the SC&rsquo;s published list. The US has no equivalent, so these
          verdicts come from a ratio screen this project built itself over SEC EDGAR
          filings &mdash; which its own documentation calls &ldquo;not a certified screening
          service&rdquo;. Business activity is approximated by SIC code, and filings cannot
          separate Islamic from conventional instruments, so both ratios are overstated.
          Errors run toward rejection.
        </p>
        <p className="text-sm text-[var(--color-muted)] mt-2">
          These are the {rows.length} symbols screened so far, newest verdict each.{" "}
          <strong>A company missing from this list has not been screened</strong> &mdash; that
          is not a finding that it is ineligible.
        </p>
      </div>

      <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm">
        <DataTable
          caption="US securities screened so far"
          rows={rows}
          rowKey={(row) => row.symbol}
          columns={US_COLUMNS}
          defaultSort={{ key: "symbol", dir: "asc" }}
          empty="Nothing has been screened yet."
        />
      </div>

      <p className="text-xs text-[var(--color-muted)]">
        Ratios are measured against a 33% limit of total assets. The verdict to act on is the
        one <code>/paper/preview</code> returns at the time of the order, not the newest row
        here.
      </p>
    </div>
  );
}

export default function ShariahUniverse() {
  const [tab, setTab] = useState("MY");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-serif font-bold text-[var(--color-text)]">
          Shariah Universe
        </h1>
        <p className="text-sm text-[var(--color-muted)] mt-1">
          What is eligible to trade, and on whose authority.{" "}
          <Link to="/market" className="text-[var(--color-accent)] hover:underline">
            Market &amp; Screening
          </Link>{" "}
          shows what is worth trading today.
        </p>
      </div>

      <div className="border-b border-[var(--color-border)] flex gap-2">
        <button
          onClick={() => setTab("MY")}
          className={tab === "MY" ? TAB_CLASS_ACTIVE : TAB_CLASS_IDLE}
        >
          Malaysia &mdash; SC approved list
        </button>
        <button
          onClick={() => setTab("US")}
          className={tab === "US" ? TAB_CLASS_ACTIVE : TAB_CLASS_IDLE}
        >
          United States &mdash; screened so far
        </button>
      </div>

      {tab === "MY" ? <MalaysiaTab /> : <UnitedStatesTab />}
    </div>
  );
}
