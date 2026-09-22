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

/** Rendering every row of a long list costs paint for no benefit: nobody reads
 *  887 rows. Search narrows; this only caps what is drawn. The counts shown to
 *  the user are always computed over the full filtered set, never this slice,
 *  so the cap can never read as "that is all there is". */
const RENDER_CAP = 100;

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

function MalaysiaTab() {
  const [universe, setUniverse] = useState(null);
  const [publication, setPublication] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [showAll, setShowAll] = useState(false);

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
    return <div className="text-[var(--color-muted)] animate-pulse">Loading the SC list...</div>;
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

  const visible = showAll ? filtered : filtered.slice(0, RENDER_CAP);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-muted)]" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search ticker or company"
            className="w-full pl-9 pr-3 py-2 bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-accent)]"
          />
        </div>
        <div className="flex gap-1">
          {["ALL", "PASS", "REJECT"].map((value) => (
            <button
              key={value}
              onClick={() => setStatusFilter(value)}
              className={
                statusFilter === value
                  ? "px-3 py-2 text-xs font-bold rounded bg-[var(--color-accent)] text-[var(--color-bg)]"
                  : "px-3 py-2 text-xs font-bold rounded bg-[var(--color-panel-2)] text-[var(--color-muted)] hover:text-[var(--color-text)] transition-colors"
              }
            >
              {value === "ALL" ? `All ${securities.length}` : null}
              {value === "PASS" ? `Compliant ${counts.pass}` : null}
              {value === "REJECT" ? `Not compliant ${counts.reject}` : null}
            </button>
          ))}
        </div>
      </div>

      <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm overflow-x-auto">
        <table className="w-full text-left text-sm whitespace-nowrap">
          <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider">
            <tr>
              <th className="px-4 py-3 font-bold">Ticker</th>
              <th className="px-4 py-3 font-bold">Company</th>
              <th className="px-4 py-3 font-bold">Board</th>
              <th className="px-4 py-3 font-bold">Sector</th>
              <th className="px-4 py-3 font-bold">Status</th>
              <th className="px-4 py-3 font-bold text-right">Ticket</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
            {!visible.length ? (
              <tr>
                <td colSpan="6" className="px-4 py-4 text-center text-[var(--color-muted)]">
                  No security matches that search.
                </td>
              </tr>
            ) : (
              visible.map((s) => (
                <tr key={s.id} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                  <td className="px-4 py-3 font-mono font-bold">{s.ticker}</td>
                  <td className="px-4 py-3">{s.issuer_name || "—"}</td>
                  <td className="px-4 py-3 text-[var(--color-muted)]">{s.board || "—"}</td>
                  <td className="px-4 py-3 text-[var(--color-muted)]">{s.sector || "—"}</td>
                  <td className="px-4 py-3">
                    <VerdictChip status={s.verdict} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    {/* Opens a pre-filled ticket. It buys nothing: the order
                        still has to clear every gate at /paper/preview, be
                        approved, and be executed with the confirmation phrase. */}
                    <Link
                      to={`/?symbol=${encodeURIComponent(s.ticker)}`}
                      className="inline-flex items-center gap-1 text-xs font-bold text-[var(--color-accent)] hover:underline"
                    >
                      Open <ArrowRight className="w-3.5 h-3.5" />
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--color-muted)]">
        <span>
          Showing {visible.length} of {filtered.length}
          {filtered.length !== securities.length ? ` (filtered from ${securities.length})` : null}
        </span>
        {filtered.length > visible.length ? (
          <button
            onClick={() => setShowAll(true)}
            className="font-bold text-[var(--color-accent)] hover:underline"
          >
            Show all {filtered.length}
          </button>
        ) : null}
      </div>

      {publication ? (
        <div className="p-4 rounded border border-[var(--color-border)] bg-[var(--color-panel-2)] text-xs text-[var(--color-muted)] space-y-1">
          <p className="font-bold text-[var(--color-subtle)] uppercase tracking-wider">
            Source document
          </p>
          <p>
            {publication.publication?.id} &middot; published{" "}
            {publication.publication?.publication_date} &middot; {publication.compliant_count}{" "}
            compliant, {publication.non_compliant_count} not compliant
          </p>
          <p>
            Approved by {publication.publication?.approved_by}, activated by{" "}
            {publication.publication?.activated_by} at {publication.publication?.activated_at}
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
    return <div className="text-[var(--color-muted)] animate-pulse">Loading screened symbols...</div>;
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

      <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm overflow-x-auto">
        <table className="w-full text-left text-sm whitespace-nowrap">
          <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider">
            <tr>
              <th className="px-4 py-3 font-bold">Symbol</th>
              <th className="px-4 py-3 font-bold">Status</th>
              <th className="px-4 py-3 font-bold">Debt</th>
              <th className="px-4 py-3 font-bold">Cash</th>
              <th className="px-4 py-3 font-bold">Filing</th>
              <th className="px-4 py-3 font-bold">Screened</th>
              <th className="px-4 py-3 font-bold text-right">Ticket</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
            {!rows.length ? (
              <tr>
                <td colSpan="7" className="px-4 py-4 text-center text-[var(--color-muted)]">
                  Nothing has been screened yet.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.symbol} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                  <td className="px-4 py-3 font-mono font-bold">{row.symbol}</td>
                  <td className="px-4 py-3">
                    <VerdictChip status={row.status} />
                  </td>
                  <td className="px-4 py-3 font-mono tabular-nums text-[var(--color-muted)]">
                    {row.debt_ratio_pct === null || row.debt_ratio_pct === undefined
                      ? "—"
                      : `${Number(row.debt_ratio_pct).toFixed(1)}%`}
                  </td>
                  <td className="px-4 py-3 font-mono tabular-nums text-[var(--color-muted)]">
                    {row.cash_ratio_pct === null || row.cash_ratio_pct === undefined
                      ? "—"
                      : `${Number(row.cash_ratio_pct).toFixed(1)}%`}
                  </td>
                  <td className="px-4 py-3 text-[var(--color-muted)]">{row.report_date || "—"}</td>
                  <td className="px-4 py-3 text-[var(--color-muted)]">
                    {row.screened_at ? row.screened_at.slice(0, 10) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      to={`/?symbol=${encodeURIComponent(row.symbol)}`}
                      className="inline-flex items-center gap-1 text-xs font-bold text-[var(--color-accent)] hover:underline"
                    >
                      Open <ArrowRight className="w-3.5 h-3.5" />
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
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
