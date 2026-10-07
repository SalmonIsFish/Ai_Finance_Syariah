import { useState, useEffect, useMemo } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { fetchMarketOverview, fetchNews, fetchStockProfile } from "../api";
import { verdictTextClass } from "../verdict";
import { marketLabel, marketBadgeClass, detectMarket } from "../market";
import { fmtMoney, fmtPct, fmtDate, MISSING, isPresent } from "../format";
import ErrorNote from "../components/ErrorNote";
import DataTable from "../components/DataTable";

/**
 * `/news` returns ai_summary as an OBJECT -- {text, model, shariah_status,
 * shariah_tradeable, generated_at} -- not a string. Rendering it directly threw
 * "Objects are not valid as a React child", and with no error boundary above it
 * that unmounted the entire app: /dashboard/market went completely blank, nav
 * included. Accepts a plain string too, in case anything older is still cached.
 *
 * Deliberately does not surface ai_summary.shariah_status as a verdict. A model
 * may describe a gate's decision; it must never look like it made one.
 */
function aiSummaryText(summary) {
  if (!summary) return null;
  if (typeof summary === "string") return summary;
  return typeof summary.text === "string" ? summary.text : null;
}

/**
 * Opens a pre-filled ticket on The Desk. It does not buy anything -- the order
 * still has to clear every gate at /paper/preview, be approved, and then be
 * executed with the confirmation phrase. A Link rather than an onClick so
 * middle-click and open-in-new-tab behave normally.
 *
 * Green only when the compliance gate actually passed. A row whose Shariah
 * verdict is REJECT or UNKNOWN still gets a link -- inspecting it on The Desk is
 * reasonable, and the gates re-run there anyway -- but it must not wear an
 * affirmative "go" colour. The UI should never look keener on a trade than the
 * gate is.
 */
function TicketLink({ cand }) {
  const pass = cand.shariah_status === "PASS";
  return (
    <Link
      to={`/?symbol=${encodeURIComponent(cand.symbol)}&price=${encodeURIComponent(cand.price)}`}
      title={
        pass
          ? `Open a pre-filled ticket for ${cand.symbol} on The Desk. This does not place an order.`
          : `${cand.symbol} is ${cand.shariah_status} on the Shariah gate. Opens a ticket for review; the gate will refuse it.`
      }
      className={`inline-flex items-center gap-1 px-3 py-1.5 rounded text-xs font-bold uppercase tracking-wider transition hover:brightness-125 focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] ${
        pass
          ? "bg-[var(--color-ok-bg)] text-[var(--color-ok)] border border-[var(--color-ok)]"
          : "bg-transparent text-[var(--color-muted)] border border-[var(--color-border-strong)]"
      }`}
    >
      {pass ? "Buy" : "Review"}
      <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
    </Link>
  );
}

const OPPORTUNITY_COLUMNS = [
  { key: "symbol", label: "Symbol", className: "font-bold" },
  {
    key: "market",
    label: "Market",
    render: (cand) => (
      <span className={`px-2 py-0.5 rounded text-xs font-bold ${marketBadgeClass()}`}>{marketLabel(cand.market)}</span>
    ),
  },
  { key: "price", label: "Price", numeric: true, render: (cand) => fmtMoney(cand.price, cand.market) },
  {
    key: "quant_signal",
    label: "Signal",
    // Green only for BUY. This column used to be green whatever the signal said.
    render: (cand) => (
      <span className={`font-bold ${cand.quant_signal === "BUY" ? "text-[var(--color-ok)]" : "text-[var(--color-muted)]"}`}>
        {cand.quant_signal || "—"}
      </span>
    ),
  },
  {
    key: "shariah_status",
    label: "Shariah",
    render: (cand) => <span className={`font-bold ${verdictTextClass(cand.shariah_status)}`}>{cand.shariah_status || "—"}</span>,
  },
  {
    key: "risk_status",
    label: "Risk",
    render: (cand) => <span className={`font-bold ${verdictTextClass(cand.risk_status)}`}>{cand.risk_status || "—"}</span>,
  },
  { key: "action", label: "Action", sortable: false, align: "right", render: (cand) => <TicketLink cand={cand} /> },
];

export default function MarketScreening() {
  const [data, setData] = useState(null);
  const [news, setNews] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Per-section failures, shown where the section would be. They used to go to
  // console.error only, leaving zero-filled tiles that looked like a quiet market.
  const [overviewError, setOverviewError] = useState(null);
  const [newsError, setNewsError] = useState(null);

  const [marketFilter, setMarketFilter] = useState("ALL");

  const [searchSymbol, setSearchSymbol] = useState("");
  const [profileData, setProfileData] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState(null);

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!searchSymbol.trim()) return;
    setProfileLoading(true);
    setProfileError(null);
    try {
      const result = await fetchStockProfile(searchSymbol.trim().toUpperCase());
      setProfileData(result);
    } catch (err) {
      setProfileError(err.message);
    } finally {
      setProfileLoading(false);
    }
  };

  useEffect(() => {
    async function load() {
      try {
        const [overviewResult, newsResult] = await Promise.allSettled([
          fetchMarketOverview(),
          fetchNews()
        ]);
        
        if (overviewResult.status === "fulfilled") {
          setData(overviewResult.value);
        } else {
          setOverviewError(overviewResult.reason);
        }

        if (newsResult.status === "fulfilled") {
          setNews(newsResult.value);
        } else {
          setNewsError(newsResult.reason);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  /**
   * Counts come from the server, which computes them over every scanned
   * candidate. `ready_candidates` is truncated to 10 before it is sent, so
   * counting the rows we happen to have been given would under-report: "2
   * Malaysian" could quietly mean five exist. Falling back to the local count
   * only when an older backend omits by_market.
   */
  const readyByMarket = useMemo(
    () => data?.by_market?.ready ?? {},
    [data]
  );

  const visibleCandidates = useMemo(() => {
    const rows = data?.ready_candidates ?? [];
    if (marketFilter === "ALL") return rows;
    // A row with no market (scanned before the field existed) is never assumed
    // to be US -- it simply does not match a specific market filter.
    return rows.filter((cand) => cand.market === marketFilter);
  }, [data, marketFilter]);

  const readyTotal = data?.by_market?.ready_total ?? (data?.ready_candidates?.length ?? 0);

  /** The profile endpoint reports the market it actually routed to; prefer that
   *  over the local guess, which is only a fallback for an older payload. */
  const profileMarket = profileData?.market ?? detectMarket(searchSymbol);

  if (loading) return <div className="text-[var(--color-muted)] animate-pulse">Loading Market &amp; Screening…</div>;
  if (error) return <div className="text-[var(--color-bad)] p-4 bg-[var(--color-bad-bg)] rounded">{error}</div>;

  const count = (v) => (isPresent(v) ? v : MISSING);

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">Market &amp; Screening</h1>
      </div>

      {overviewError && <ErrorNote what="the market overview" error={overviewError} />}

      <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded p-4">
          <div className="text-sm font-bold text-[var(--color-subtle)] uppercase tracking-wider mb-2">Watchlist Coverage</div>
          <div className="text-xl font-mono text-[var(--color-text)] tabular-nums">{fmtPct(data?.latest_scan?.coverage_pct, 1)}</div>
          <div className="text-xs text-[var(--color-muted)] mt-1">{count(data?.watchlist?.count)} symbols tracked</div>
        </div>
        <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded p-4">
          <div className="text-sm font-bold text-[var(--color-subtle)] uppercase tracking-wider mb-2">Ready Candidates</div>
          <div className="text-xl font-mono text-[var(--color-text)] tabular-nums">{count(data?.counts?.ready)}</div>
        </div>
        <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded p-4">
          <div className="text-sm font-bold text-[var(--color-subtle)] uppercase tracking-wider mb-2">Active Alerts</div>
          <div className="text-xl font-mono text-[var(--color-text)] tabular-nums">{count(data?.counts?.alerts)}</div>
        </div>
      </section>

      <section>
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h2 className="text-lg font-bold text-[var(--color-text)]">Opportunities (Ready)</h2>
          <div className="flex gap-1">
            {["ALL", "MY", "US"].map((value) => (
              <button
                key={value}
                onClick={() => setMarketFilter(value)}
                className={
                  marketFilter === value
                    ? "px-3 py-1.5 text-xs font-bold rounded bg-[var(--color-accent)] text-[var(--color-bg)]"
                    : "px-3 py-1.5 text-xs font-bold rounded bg-[var(--color-panel-2)] text-[var(--color-muted)] hover:text-[var(--color-text)] transition-colors"
                }
              >
                {value === "ALL" ? "All markets" : null}
                {value === "MY" ? `Malaysia ${readyByMarket.MY ?? 0}` : null}
                {value === "US" ? `US ${readyByMarket.US ?? 0}` : null}
              </button>
            ))}
          </div>
        </div>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm">
          <DataTable
            caption="Ready opportunities"
            rows={visibleCandidates}
            rowKey={(cand) => cand.symbol}
            columns={OPPORTUNITY_COLUMNS}
            empty={
              /* A Malaysia filter that finds nothing is almost always a
                 watchlist fact, not a market fact -- the scanner's default
                 universe is entirely US. Saying "no ready opportunities" there
                 would read as a broken feature. */
              marketFilter === "MY" && !(readyByMarket.MY ?? 0)
                ? "No Malaysian symbols are in the current watchlist, so none were scanned."
                : "No ready opportunities right now."
            }
          />
        </div>
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          Showing {visibleCandidates.length} of {readyTotal} ready candidates
          {readyTotal > (data?.ready_candidates?.length ?? 0)
            ? " (the server sends at most 10)"
            : null}
          .
        </p>
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          These verdicts come from the last watchlist scan. <span className="text-[var(--color-text)]">Buy</span> opens
          a pre-filled ticket on The Desk — it does not place an order. Every gate is
          re-run against live state at preview, again at approval, and execution still
          requires the confirmation phrase.
        </p>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Stock Profile</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-6 space-y-6">
          <form onSubmit={handleSearch} className="flex gap-4">
            <input
              type="text"
              value={searchSymbol}
              onChange={(e) => setSearchSymbol(e.target.value)}
              placeholder="Enter symbol (e.g. AAPL)"
              className="bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded px-4 py-2 text-[var(--color-text)] uppercase w-64 focus:outline-none focus:border-[var(--color-accent)]"
            />
            <button
              type="submit"
              disabled={profileLoading || !searchSymbol.trim()}
              className="bg-[var(--color-accent)] text-[var(--color-button-text)] px-6 py-2 rounded font-medium hover:bg-[var(--color-accent-2)] transition-colors disabled:opacity-50"
            >
              {profileLoading ? "Loading…" : "Search"}
            </button>
          </form>

          {profileError && (
            <div className="p-3 bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)] rounded text-sm">
              {profileError}
            </div>
          )}

          {profileData && !profileLoading && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-panel-2)]">
                <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Shariah Status</span>
                <div className={`text-lg font-bold ${verdictTextClass(profileData.shariah?.status)}`}>
                  {profileData.shariah?.status || 'UNKNOWN'}
                </div>
                <div className="text-xs text-[var(--color-muted)] mt-1 line-clamp-2">{profileData.shariah?.reason || 'No data'}</div>
              </div>
              
              <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-panel-2)]">
                <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Market Data</span>
                <div className="text-lg font-mono tabular-nums text-[var(--color-text)]">
                  {fmtMoney(profileData.market_data?.latest_close, profileMarket)}
                </div>
                <div className="text-xs text-[var(--color-muted)] mt-1">{profileData.market_data?.bars || 0} bars • {profileData.market_data?.source || 'N/A'}</div>
              </div>

              <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-panel-2)]">
                <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Latest Opportunity</span>
                <div className="text-lg font-bold text-[var(--color-text)]">
                  {profileData.latest_opportunity?.watch_status || 'NONE'}
                </div>
                <div className="text-xs text-[var(--color-muted)] mt-1 font-mono tabular-nums">Trigger: {fmtMoney(profileData.latest_opportunity?.trigger_price, profileMarket)}</div>
              </div>

              <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-panel-2)]">
                <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Portfolio Exposure</span>
                <div className="text-lg font-mono tabular-nums text-[var(--color-text)]">
                  {fmtPct(profileData.portfolio?.account_exposure_pct)}
                </div>
                <div className="text-xs text-[var(--color-muted)] mt-1 font-mono tabular-nums">{count(profileData.portfolio?.quantity)} shares</div>
              </div>
            </div>
          )}
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Latest News</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {newsError ? (
            <div className="col-span-2"><ErrorNote what="news" error={newsError} /></div>
          ) : !news?.news?.length ? (
            <div className="text-[var(--color-muted)] text-sm col-span-2">No recent news found.</div>
          ) : (
            news.news.slice(0, 6).map((item, idx) => (
              <a key={idx} href={item.url} target="_blank" rel="noopener noreferrer" className="block bg-[var(--color-panel)] border border-[var(--color-border)] rounded p-4 hover:border-[var(--color-accent)] transition-colors">
                <div className="flex justify-between items-start mb-2">
                  <span className="font-bold text-[var(--color-text)]">{item.symbols?.join(", ")}</span>
                  <span className="text-xs text-[var(--color-muted)]">{fmtDate(item.created_at)}</span>
                </div>
                <h3 className="text-sm font-bold text-[var(--color-text)] mb-2 line-clamp-2">{item.headline}</h3>
                {item.summary && <p className="text-xs text-[var(--color-subtle)] line-clamp-3">{item.summary}</p>}
                {aiSummaryText(item.ai_summary) && (
                  <div className="mt-2 text-xs text-[var(--color-accent)] font-medium">
                    AI: {aiSummaryText(item.ai_summary)}
                    {item.ai_summary?.model && (
                      <span className="ml-1 text-[var(--color-muted)] font-normal">
                        ({item.ai_summary.model})
                      </span>
                    )}
                  </div>
                )}
              </a>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
