import { useState, useEffect } from "react";
import { fetchPortfolio, fetchLivePositions, fetchAccount, fetchPortfolioHistoryLive, fetchPortfolioHistory, fetchCompliance } from "../api";
import { verdictBadgeClass, verdictTextClass } from "../verdict";
import { fmtMoney, fmtPct, fmtNum, fmtQty, fmtDate, fmtTime, isPresent } from "../format";
import useResource from "../useResource";
import ErrorNote from "../components/ErrorNote";

/**
 * Re-screens what is actually held against the current Shariah authority.
 *
 * Order-time screening cannot catch a security the SC reclassifies after you
 * bought it, and the list updates on a schedule (last Friday of May and
 * November). This panel is the standing answer to "is what I hold still
 * compliant?".
 *
 * NON_COMPLIANT and UNCONFIRMED are shown separately and never share a colour.
 * A reclassified holding carries a disposal duty and a purification obligation;
 * a holding merely absent from the publication carries neither, and presenting
 * them alike would invite a sale the authority never asked for.
 */
function CompliancePanel({ compliance }) {
  if (!compliance) {
    return (
      <div className="text-[var(--color-muted)] text-sm">
        Compliance screening unavailable.
      </div>
    );
  }

  const { screening, purification } = compliance;
  const nonCompliant = (screening.flagged || []).filter(h => h.alert === "NON_COMPLIANT_HOLDING");
  const unconfirmed = (screening.flagged || []).filter(h => h.alert === "UNCONFIRMED_HOLDING");

  if (screening.position_count === 0) {
    return <div className="text-[var(--color-muted)] text-sm">No open positions to screen.</div>;
  }

  return (
    <div className="space-y-4">
      {screening.flagged_count === 0 ? (
        <div className={`px-3 py-2 rounded text-sm font-medium ${verdictBadgeClass("PASS")}`}>
          All {screening.position_count} holding{screening.position_count === 1 ? "" : "s"} confirmed
          compliant by the active publication.
        </div>
      ) : (
        <div className={`px-3 py-2 rounded text-sm font-medium ${verdictBadgeClass("WARN")}`}>
          {screening.flagged_count} of {screening.position_count} holdings need attention.
        </div>
      )}

      {nonCompliant.length > 0 && (
        <div>
          <h3 className={`text-xs font-bold uppercase tracking-wider mb-2 ${verdictTextClass("REJECT")}`}>
            Non-compliant — the authority says these are not eligible
          </h3>
          <div className="space-y-2">
            {nonCompliant.map(h => (
              <div key={`${h.symbol}-${h.account_suffix}`} className={`rounded p-3 text-sm ${verdictBadgeClass("REJECT")}`}>
                <div className="flex justify-between font-bold">
                  <span>{h.symbol}</span>
                  <span className="font-mono tabular-nums">{h.quantity}</span>
                </div>
                <div className="text-xs mt-1 opacity-90">
                  cost {fmtNum(h.cost_basis, 2)} · per {h.publication_id || "active publication"}
                </div>
                {/* The month, as a date rather than a sentence. Until the clock
                    was persisted, a holding flagged a year ago looked exactly
                    like one flagged this morning. */}
                {h.disposal_deadline && (
                  <div className="text-xs mt-1 font-bold">
                    {h.overdue
                      ? `OVERDUE — disposal was due ${h.disposal_deadline} (${Math.abs(h.days_remaining)} days ago)`
                      : `Dispose by ${h.disposal_deadline} — ${h.days_remaining} days left`}
                    <span className="block font-normal opacity-80">
                      {h.deadline_basis === "publication_date"
                        ? "counted from the publication date"
                        : "counted from when this was first observed; the publication carried no date, so the real deadline may be earlier"}
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
          <p className="text-xs text-[var(--color-muted)] mt-2">
            Disposal ruling: sell within one month, recover cost only; anything above cost goes to
            baitulmal. Confirm against the SC paper — this reports, it does not decide. The
            deadline counts from the SC's ruling, not from when this system noticed it.
          </p>
        </div>
      )}

      {unconfirmed.length > 0 && (
        <div>
          <h3 className={`text-xs font-bold uppercase tracking-wider mb-2 ${verdictTextClass("UNKNOWN")}`}>
            Unconfirmed — not a ruling
          </h3>
          <div className="space-y-2">
            {unconfirmed.map(h => (
              <div key={`${h.symbol}-${h.account_suffix}`} className={`rounded p-3 text-sm ${verdictBadgeClass("UNKNOWN")}`}>
                <div className="flex justify-between font-bold">
                  <span>{h.symbol}</span>
                  <span className="font-mono tabular-nums">{h.quantity}</span>
                </div>
                <div className="text-xs mt-1 opacity-90">{h.reason}</div>
              </div>
            ))}
          </div>
          <p className="text-xs text-[var(--color-muted)] mt-2">
            No authority has ruled on these, so no divestment duty arises. Check whether the ticker
            was renumbered or the position is stale.
          </p>
        </div>
      )}

      {purification && purification.entries?.length > 0 && (
        <div className="border-t border-[var(--color-border)] pt-3">
          <div className="flex justify-between text-sm">
            <span className="text-[var(--color-muted)]">Purification owed (estimate)</span>
            <span className="font-mono tabular-nums font-bold text-[var(--color-text)]">
              {fmtNum(purification.total_purification_due, 2)}
            </span>
          </div>
          {!purification.complete && (
            <div className="text-xs text-[var(--color-warn)] mt-1">
              Incomplete — no price for {purification.unpriced.join(", ")}. The true figure is
              higher than shown.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

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
        <div className="text-2xl font-mono tabular-nums">{fmtMoney(lastVal, "US")}</div>
        <div className={`font-mono text-sm tabular-nums ${isPositive ? 'text-[var(--color-ok)]' : 'text-[var(--color-bad)]'}`}>
          {/* A zero first value would divide to Infinity; fmtPct renders that as "—". */}
          {fmtPct(((lastVal - firstVal) / firstVal) * 100, 2, { signed: true })}
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
        <span>{fmtDate(data[0].date)}</span>
        <span>{fmtDate(data[data.length - 1].date)}</span>
      </div>
    </div>
  );
}

/** "as of 15:24:19 MYT", turning amber and saying so when refreshes have been failing. */
function AsOf({ resource }) {
  if (!resource.asOf) return null;
  return (
    <span
      className={`text-xs font-normal ml-3 ${resource.stale || resource.error ? "text-[var(--color-warn)]" : "text-[var(--color-muted)]"}`}
      title={resource.error ? `Last refresh failed: ${resource.error.message}` : undefined}
    >
      {resource.stale || resource.error ? "stale · " : ""}as of {fmtTime(resource.asOf)}
    </span>
  );
}

const LIVE_POLL_MS = 30_000;

export default function PortfolioRisk() {
  // The broker-backed figures refresh themselves and carry their own time. They
  // used to be fetched once on mount, so a balance loaded at 09:00 still looked
  // current at 15:00.
  const accountRes = useResource(fetchAccount, { intervalMs: LIVE_POLL_MS });
  const positionsRes = useResource(fetchLivePositions, { intervalMs: LIVE_POLL_MS });

  const [data, setData] = useState({ portfolio: null, compliance: null });
  const [errors, setErrors] = useState({});
  const [historyData, setHistoryData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const [portfolioResult, complianceResult] = await Promise.allSettled([
          fetchPortfolio(),
          fetchCompliance()
        ]);

        // Each section keeps its own failure, and shows it. These used to go to
        // console.error only, so a failed broker call rendered as $0.00 equity
        // and an empty positions table -- quiet, plausible and false.
        const value = (r) => (r.status === "fulfilled" ? r.value : null);
        const failure = (r) => (r.status === "rejected" ? r.reason : null);
        const portfolio = value(portfolioResult);
        const compliance = value(complianceResult);

        let hist = null;
        let histError = null;
        try {
          hist = await fetchPortfolioHistoryLive();
          if (hist.status !== "ok") throw new Error(`Live history failed: ${hist.status}`);
        } catch {
          try {
            hist = await fetchPortfolioHistory();
          } catch (e2) {
            hist = null;
            histError = e2;
          }
        }

        setData({ portfolio, compliance });
        setErrors({
          portfolio: failure(portfolioResult),
          compliance: failure(complianceResult),
          history: histError,
        });
        setHistoryData(hist);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <div className="text-[var(--color-muted)] animate-pulse">Loading Portfolio &amp; Risk…</div>;
  if (error) return <div className="text-[var(--color-bad)] p-4 bg-[var(--color-bad-bg)] rounded">{error}</div>;

  const { portfolio, compliance } = data;
  const account = accountRes.data;
  const positions = positionsRes.data;
  // A refresh that fails keeps the last good figures on screen (with a stale
  // "as of"); only with nothing good to show is the failure the whole story.
  const accountError = account ? null : accountRes.error;
  const positionsError = positions ? null : positionsRes.error;
  const limits = portfolio?.risk_limits || {};
  // /paper/account answers 200 with a status and no figures when the broker is
  // unreachable, so a missing equity is a failure too, not just a rejected fetch.
  const accountUnavailable = !accountRes.loading && (accountError || !isPresent(account?.equity));
  // Alpaca is the only broker behind these balances, so they are US dollars.
  const usd = (v) => fmtMoney(v, "US");

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif text-[var(--color-text)]">Portfolio & Risk</h1>
      </div>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Account Balances<AsOf resource={accountRes} /></h2>
        {accountUnavailable && (
          <div className="mb-4">
            <ErrorNote
              what="the broker account"
              error={accountError || (account?.status ? `broker reported ${account.status}` : null)}
            />
          </div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Equity" value={usd(account?.equity)} />
          <StatCard label="Cash" value={usd(account?.cash)} />
          <StatCard label="Buying Power" value={usd(account?.buying_power)} />
          <StatCard label="Total Exposure" value={usd(portfolio?.total_exposure)} />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Portfolio Value History (1M)</h2>
        <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-6">
          {errors.history && <ErrorNote what="portfolio history" error={errors.history} />}
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
            {errors.portfolio ? (
              <ErrorNote what="the portfolio" error={errors.portfolio} />
            ) : portfolio?.positions?.length === 0 ? (
              <div className="text-[var(--color-muted)] text-sm">No open positions.</div>
            ) : (
              portfolio?.positions?.map(pos => (
                <div key={pos.symbol} className="flex justify-between items-center text-sm border-b border-[var(--color-border)] pb-2 last:border-0 last:pb-0">
                  <span className="font-bold text-[var(--color-text)]">{pos.symbol}</span>
                  <span className="font-mono text-[var(--color-muted)] tabular-nums">{fmtPct(pos.account_exposure_pct)}</span>
                </div>
              ))
            )}
          </div>
        </div>

        <div>
          <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Shariah Compliance of Holdings</h2>
          <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-4 mb-6">
            {errors.compliance ? (
              <ErrorNote what="holdings compliance" error={errors.compliance} />
            ) : (
              <CompliancePanel compliance={compliance} />
            )}
          </div>

          <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Risk Policy</h2>
          <div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-md shadow-sm p-4 space-y-3">
             {errors.portfolio && <ErrorNote what="the risk limits" error={errors.portfolio} />}
             {[
               ["Max Position Size", limits.max_position_pct],
               ["Max Total Exposure", limits.max_total_exposure_pct],
               ["Max Loss Per Trade", limits.max_loss_per_trade_pct],
               ["Max Daily Loss", limits.max_daily_loss_pct],
               ["Max Sector Exposure", limits.max_sector_exposure_pct],
             ].map(([label, value]) => (
               <div key={label} className="flex justify-between text-sm border-b border-[var(--color-border)] pb-2 last:border-0 last:pb-0">
                 <span className="text-[var(--color-muted)]">{label}</span>
                 <span className="font-mono text-[var(--color-text)] tabular-nums">{fmtPct(value)}</span>
               </div>
             ))}
          </div>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-bold text-[var(--color-text)] mb-4">Live Broker Positions<AsOf resource={positionsRes} /></h2>
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
              {/* A failed fetch used to fall through both branches and render no
                  rows at all. Worse, an unreachable broker answers 200 with
                  status "unreachable" and an EMPTY list, which rendered as "No
                  live positions found" -- an outage presented as an empty account.
                  Only status "ok" means the list is the broker's answer. */}
              {positionsRes.loading && !positions ? (
                <tr>
                  <td colSpan="4" className="px-4 py-4 text-center text-[var(--color-muted)]">Loading live positions…</td>
                </tr>
              ) : positionsError || positions?.status !== "ok" || !Array.isArray(positions?.positions) ? (
                <tr>
                  <td colSpan="4" className="px-4 py-4">
                    <ErrorNote
                      what="live broker positions"
                      error={positionsError || (positions?.status ? `broker reported ${positions.status}${positions.reason ? ` (${positions.reason})` : ""}` : null)}
                    />
                  </td>
                </tr>
              ) : positions.positions.length === 0 ? (
                <tr>
                  <td colSpan="4" className="px-4 py-4 text-center text-[var(--color-muted)]">No live positions found.</td>
                </tr>
              ) : (
                // Field names as fetch_broker_positions emits them: `quantity` and
                // `unrealized_pnl`. This read `qty` and `unrealized_pl` (Alpaca's raw
                // names, which the backend renames), so every row showed a blank
                // quantity and "$NaN" P&L.
                positions.positions.map(pos => (
                  <tr key={pos.symbol} className="hover:bg-[var(--color-bg-soft)] transition-colors">
                    <td className="px-4 py-3 font-bold">{pos.symbol}</td>
                    <td className="px-4 py-3 font-mono tabular-nums">{fmtQty(pos.quantity)}</td>
                    <td className="px-4 py-3 font-mono tabular-nums">{usd(pos.market_value)}</td>
                    <td className={`px-4 py-3 font-mono tabular-nums ${!isPresent(pos.unrealized_pnl) ? '' : Number(pos.unrealized_pnl) >= 0 ? 'text-[var(--color-ok)]' : 'text-[var(--color-bad)]'}`}>
                      {fmtMoney(pos.unrealized_pnl, "US", { signed: true })}
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
