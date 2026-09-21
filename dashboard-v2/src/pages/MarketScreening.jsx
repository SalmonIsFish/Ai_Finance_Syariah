import { useState, useEffect } from "react";
import { fetchMarketOverview, fetchNews, fetchStockProfile } from "../api";
import { verdictTextClass } from "../verdict";

export default function MarketScreening() {
  const [data, setData] = useState(null);
  const [news, setNews] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

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
          console.error("Market overview failed:", overviewResult.reason);
        }
        
        if (newsResult.status === "fulfilled") {
          setNews(newsResult.value);
        } else {
          console.error("News failed:", newsResult.reason);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <div className="text-[var(--color-muted)] animate-pulse">Loading Market & Screening...</div>;
  if (error) return <div className="text-[var(--color-bad)] p-4 bg-[var(--color-bad-bg)] rounded">{error}</div>;

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">Market & Screening</h1>
      </div>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded p-4">
          <div className="text-sm font-bold text-[var(--color-subtle)] uppercase tracking-wider mb-2">Watchlist Coverage</div>
          <div className="text-xl font-mono text-[var(--color-text)] tabular-nums">{data?.latest_scan?.coverage_pct?.toFixed(1) || 0}%</div>
          <div className="text-xs text-[var(--color-muted)] mt-1">{data?.watchlist?.count} symbols tracked</div>
        </div>
        <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded p-4">
          <div className="text-sm font-bold text-[var(--color-subtle)] uppercase tracking-wider mb-2">Ready Candidates</div>
          <div className="text-xl font-mono text-[var(--color-text)] tabular-nums">{data?.counts?.ready || 0}</div>
        </div>
        <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded p-4">
          <div className="text-sm font-bold text-[var(--color-subtle)] uppercase tracking-wider mb-2">Active Alerts</div>
          <div className="text-xl font-mono text-[var(--color-text)] tabular-nums">{data?.counts?.alerts || 0}</div>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Opportunities (Ready)</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm overflow-x-auto">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider">
              <tr>
                <th className="px-4 py-3 font-bold">Symbol</th>
                <th className="px-4 py-3 font-bold">Price</th>
                <th className="px-4 py-3 font-bold">Signal</th>
                <th className="px-4 py-3 font-bold">Shariah</th>
                <th className="px-4 py-3 font-bold">Risk</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
              {!data?.ready_candidates?.length ? (
                <tr>
                  <td colSpan="5" className="px-4 py-4 text-center text-[var(--color-muted)]">No ready opportunities right now.</td>
                </tr>
              ) : (
                data.ready_candidates.map(cand => (
                  <tr key={cand.symbol} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                    <td className="px-4 py-3 font-bold">{cand.symbol}</td>
                    <td className="px-4 py-3 font-mono tabular-nums">${Number(cand.price).toFixed(2)}</td>
                    <td className="px-4 py-3 font-bold text-[var(--color-ok)]">{cand.quant_signal}</td>
                    <td className={`px-4 py-3 font-bold ${verdictTextClass(cand.shariah_status)}`}>{cand.shariah_status}</td>
                    <td className={`px-4 py-3 font-bold ${verdictTextClass(cand.risk_status)}`}>{cand.risk_status}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
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
              {profileLoading ? "Loading..." : "Search"}
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
                  ${Number(profileData.market_data?.latest_close || 0).toFixed(2)}
                </div>
                <div className="text-xs text-[var(--color-muted)] mt-1">{profileData.market_data?.bars || 0} bars • {profileData.market_data?.source || 'N/A'}</div>
              </div>

              <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-panel-2)]">
                <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Latest Opportunity</span>
                <div className="text-lg font-bold text-[var(--color-text)]">
                  {profileData.latest_opportunity?.watch_status || 'NONE'}
                </div>
                <div className="text-xs text-[var(--color-muted)] mt-1 font-mono tabular-nums">Trigger: ${Number(profileData.latest_opportunity?.trigger_price || 0).toFixed(2)}</div>
              </div>

              <div className="border border-[var(--color-border)] rounded p-4 bg-[var(--color-panel-2)]">
                <span className="block text-[var(--color-subtle)] uppercase tracking-wider text-xs font-bold mb-1">Portfolio Exposure</span>
                <div className="text-lg font-mono tabular-nums text-[var(--color-text)]">
                  {profileData.portfolio?.account_exposure_pct?.toFixed(2) || '0.00'}%
                </div>
                <div className="text-xs text-[var(--color-muted)] mt-1 font-mono tabular-nums">{profileData.portfolio?.quantity || 0} shares</div>
              </div>
            </div>
          )}
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Latest News</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {!news?.news?.length ? (
            <div className="text-[var(--color-muted)] text-sm col-span-2">No recent news found.</div>
          ) : (
            news.news.slice(0, 6).map((item, idx) => (
              <a key={idx} href={item.url} target="_blank" rel="noopener noreferrer" className="block bg-[var(--color-panel)] border border-[var(--color-border)] rounded p-4 hover:border-[var(--color-accent)] transition-colors">
                <div className="flex justify-between items-start mb-2">
                  <span className="font-bold text-[var(--color-text)]">{item.symbols?.join(", ")}</span>
                  <span className="text-xs text-[var(--color-muted)]">{new Date(item.created_at).toLocaleDateString()}</span>
                </div>
                <h3 className="text-sm font-bold text-[var(--color-text)] mb-2 line-clamp-2">{item.headline}</h3>
                {item.summary && <p className="text-xs text-[var(--color-subtle)] line-clamp-3">{item.summary}</p>}
                {item.ai_summary && <div className="mt-2 text-xs text-[var(--color-accent)] font-medium">AI: {item.ai_summary}</div>}
              </a>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
