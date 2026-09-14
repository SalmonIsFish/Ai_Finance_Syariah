import { useState, useEffect } from "react";
import { fetchPortfolio, fetchLivePositions, fetchAccount, fetchPortfolioHistoryLive, fetchPortfolioHistory } from "../api";

function StatCard({ label, value }) {
  return (
    <div className="bg-[var(--color-panel-2)] border border-[var(--color-border)] rounded p-4">
      <div className="text-sm font-bold text-[var(--color-subtle)] uppercase tracking-wider mb-2">{label}</div>
      <div className="text-xl font-mono text-[var(--color-text)] tabular-nums">{value}</div>
    </div>
  );
}

function SimpleLineChart({ data }) {
  if (!data || data.length === 0) return <div className="text-[var(--color-muted)] text-sm p-4">No history data available.</div>;
  
  const values = data.map(d => d.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  
  const height = 150;
  const width = 800; // Will scale with viewBox
  
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1 || 1)) * width;
    const y = height - ((d.value - min) / range) * height;
    return `${x},${y}`;
  }).join(" ");
  
  const firstVal = data[0].value;
  const lastVal = data[data.length - 1].value;
  const isPositive = lastVal >= firstVal;

  return (
    <div className="w-full">
      <div className="flex justify-between items-end mb-2">
        <div className="text-2xl font-mono tabular-nums">${lastVal.toFixed(2)}</div>
        <div className={`font-mono text-sm tabular-nums ${isPositive ? 'text-[var(--color-ok)]' : 'text-[var(--color-bad)]'}`}>
          {isPositive ? '+' : ''}{((lastVal - firstVal) / firstVal * 100).toFixed(2)}%
        </div>
      </div>
      <svg viewBox={`0 -10 ${width} ${height + 20}`} className="w-full h-32 overflow-visible" preserveAspectRatio="none">
        <polyline 
          fill="none" 
          stroke={isPositive ? "var(--color-ok)" : "var(--color-bad)"} 
          strokeWidth="2" 
          points={points} 
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between mt-2 text-[var(--color-muted)] text-xs">
        <span>{new Date(data[0].date).toLocaleDateString()}</span>
        <span>{new Date(data[data.length - 1].date).toLocaleDateString()}</span>
      </div>
    </div>
  );
}

export default function PortfolioRisk() {
  const [data, setData] = useState({ portfolio: null, positions: null, account: null });
  const [historyData, setHistoryData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const [portfolioResult, positionsResult, accountResult] = await Promise.allSettled([
          fetchPortfolio(),
          fetchLivePositions(),
          fetchAccount()
        ]);
        
        let portfolio = portfolioResult.status === "fulfilled" ? portfolioResult.value : null;
        if (portfolioResult.status === "rejected") console.error("fetchPortfolio failed:", portfolioResult.reason);

        let positions = positionsResult.status === "fulfilled" ? positionsResult.value : null;
        if (positionsResult.status === "rejected") console.error("fetchLivePositions failed:", positionsResult.reason);

        let account = accountResult.status === "fulfilled" ? accountResult.value : null;
        if (accountResult.status === "rejected") console.error("fetchAccount failed:", accountResult.reason);
        
        let hist = null;
        try {
          hist = await fetchPortfolioHistoryLive();
          if (hist.status !== "ok") throw new Error(`Live history failed: ${hist.status}`);
        } catch (e) {
          try {
            hist = await fetchPortfolioHistory();
          } catch (e2) {
            console.error("fetchPortfolioHistory failed:", e2);
          }
        }
        
        setData({ portfolio, positions, account });
        setHistoryData(hist);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <div className="text-[var(--color-muted)] animate-pulse">Loading Portfolio & Risk...</div>;
  if (error) return <div className="text-[var(--color-bad)] p-4 bg-[var(--color-bad-bg)] rounded">{error}</div>;

  const { portfolio, positions, account } = data;
  const limits = portfolio?.risk_limits || {};

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">Portfolio & Risk</h1>
      </div>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Account Balances</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Equity" value={`$${Number(account?.equity || 0).toFixed(2)}`} />
          <StatCard label="Cash" value={`$${Number(account?.cash || 0).toFixed(2)}`} />
          <StatCard label="Buying Power" value={`$${Number(account?.buying_power || 0).toFixed(2)}`} />
          <StatCard label="Total Exposure" value={`$${Number(portfolio?.total_exposure || 0).toFixed(2)}`} />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Portfolio Value History (1M)</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-6">
          {historyData && (() => {
            let chartData = [];
            if (historyData.timestamps && historyData.equity) {
              chartData = historyData.timestamps.map((ts, i) => ({
                date: ts * 1000,
                value: historyData.equity[i]
              })).filter(d => d.value !== null);
            } else if (historyData.snapshots) {
              chartData = historyData.snapshots.map(s => ({
                date: s.captured_at,
                value: s.account_equity
              }));
            }
            return <SimpleLineChart data={chartData} />;
          })()}
        </div>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Current Allocation</h2>
          <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-4 space-y-3">
            {portfolio?.positions?.length === 0 ? (
              <div className="text-[var(--color-muted)] text-sm">No open positions.</div>
            ) : (
              portfolio?.positions?.map(pos => (
                <div key={pos.symbol} className="flex justify-between items-center text-sm border-b border-[var(--color-border)] pb-2 last:border-0 last:pb-0">
                  <span className="font-bold text-[var(--color-text)]">{pos.symbol}</span>
                  <span className="font-mono text-[var(--color-muted)] tabular-nums">{pos.account_exposure_pct?.toFixed(2)}%</span>
                </div>
              ))
            )}
          </div>
        </div>

        <div>
          <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Risk Policy</h2>
          <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-4 space-y-3">
             <div className="flex justify-between text-sm border-b border-[var(--color-border)] pb-2">
               <span className="text-[var(--color-muted)]">Max Position Size</span>
               <span className="font-mono text-[var(--color-text)] tabular-nums">{limits.max_position_pct}%</span>
             </div>
             <div className="flex justify-between text-sm border-b border-[var(--color-border)] pb-2">
               <span className="text-[var(--color-muted)]">Max Total Exposure</span>
               <span className="font-mono text-[var(--color-text)] tabular-nums">{limits.max_total_exposure_pct}%</span>
             </div>
             <div className="flex justify-between text-sm border-b border-[var(--color-border)] pb-2">
               <span className="text-[var(--color-muted)]">Max Loss Per Trade</span>
               <span className="font-mono text-[var(--color-text)] tabular-nums">{limits.max_loss_per_trade_pct}%</span>
             </div>
             <div className="flex justify-between text-sm border-b border-[var(--color-border)] pb-2">
               <span className="text-[var(--color-muted)]">Max Daily Loss</span>
               <span className="font-mono text-[var(--color-text)] tabular-nums">{limits.max_daily_loss_pct}%</span>
             </div>
             <div className="flex justify-between text-sm">
               <span className="text-[var(--color-muted)]">Max Sector Exposure</span>
               <span className="font-mono text-[var(--color-text)] tabular-nums">{limits.max_sector_exposure_pct}%</span>
             </div>
          </div>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Live Broker Positions</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm overflow-x-auto">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-[var(--color-panel-2)] border-b border-[var(--color-border)] text-[var(--color-subtle)] text-xs uppercase tracking-wider">
              <tr>
                <th className="px-4 py-3 font-bold">Symbol</th>
                <th className="px-4 py-3 font-bold">Qty</th>
                <th className="px-4 py-3 font-bold">Market Value</th>
                <th className="px-4 py-3 font-bold">Unrealized P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-text)]">
              {positions?.positions?.length === 0 ? (
                <tr>
                  <td colSpan="4" className="px-4 py-4 text-center text-[var(--color-muted)]">No live positions found.</td>
                </tr>
              ) : (
                positions?.positions?.map(pos => (
                  <tr key={pos.symbol} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                    <td className="px-4 py-3 font-bold">{pos.symbol}</td>
                    <td className="px-4 py-3 font-mono tabular-nums">{pos.qty}</td>
                    <td className="px-4 py-3 font-mono tabular-nums">${Number(pos.market_value).toFixed(2)}</td>
                    <td className={`px-4 py-3 font-mono tabular-nums ${Number(pos.unrealized_pl) >= 0 ? 'text-[var(--color-ok)]' : 'text-[var(--color-bad)]'}`}>
                      ${Number(pos.unrealized_pl).toFixed(2)}
                    </td>
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
