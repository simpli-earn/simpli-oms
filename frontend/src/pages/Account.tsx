import { Link } from "react-router-dom";
import { useQuery, type Account, type AccountPosition, type Fill, type StrategyPosition } from "../api";
import { money, price, shares, tone, when } from "../format";
import { Banner, Empty, PageHeader } from "../Shell";

export function Account() {
  const account = useQuery<Account>("/api/account");
  const positions = useQuery<AccountPosition[]>("/api/positions");
  const strategyPositions = useQuery<StrategyPosition[]>("/api/strategy-positions");
  const fills = useQuery<Fill[]>("/api/fills?limit=12");
  const portfolioValue = account.data?.portfolio_value ?? account.data?.equity;
  const accountCash = account.data?.cash;

  return (
    <div className="portfolio-dashboard">
      <PageHeader
        title="Portfolio"
        lede="Shared Alpaca paper account, with every OMS fill and held position attributed back to its strategy."
      >
        <Link className="new-order" to="/order">New order</Link>
      </PageHeader>
      {account.error ? <Banner>Account balances are temporarily unavailable: {account.error}</Banner> : null}
      <section className="portfolio-hero">
        <div>
          <span className="eyebrow">Portfolio value</span>
          <strong>{money(portfolioValue)}</strong>
          <p>{account.data ? `Paper account · ${account.data.status}` : "Loading account…"}</p>
        </div>
        <div className="hero-meta">
          <div><span>Cash</span><strong>{money(accountCash)}</strong></div>
          <div><span>Buying power</span><strong>{money(account.data?.buying_power)}</strong></div>
          <div><span>Open positions</span><strong>{positions.data?.length ?? "—"}</strong></div>
        </div>
      </section>

      <section className="portfolio-grid">
        <div className="panel balance-panel">
          <div className="panel-head"><h2>Balances</h2><span className="paper-pill">Paper</span></div>
          <div className="balance-list">
            <Balance label="Equity" value={money(account.data?.equity)} />
            <Balance label="Portfolio value" value={money(portfolioValue)} />
            <Balance label="Cash" value={money(accountCash)} />
            <Balance label="Buying power" value={money(account.data?.buying_power)} />
          </div>
        </div>
        <div className="panel strategy-panel">
          <div className="panel-head"><h2>Strategy attribution</h2><Link to="/strategies">View strategies</Link></div>
          <p className="attribution-copy">Position tags are from OMS fills, not inferred from the shared brokerage account.</p>
          <div className="strategy-summary">
            <strong>{strategyPositions.data?.length ?? "—"}</strong>
            <span>open strategy positions</span>
          </div>
          {strategyPositions.error ? <Banner>{strategyPositions.error}</Banner> : null}
        </div>
      </section>

      <section className="panel position-panel">
        <div className="panel-head">
          <div><h2>Top positions</h2><p>Account positions with OMS strategy allocation</p></div>
          <span className="muted">{positions.data?.length ?? 0} total</span>
        </div>
        {positions.error ? <Banner>Positions could not be loaded from Alpaca: {positions.error}</Banner> : null}
        {!positions.loading && positions.data && positions.data.length === 0 ? (
          <Empty>Your paper account has no open positions.</Empty>
        ) : null}
        {positions.data && positions.data.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th className="num">Qty</th>
                <th className="num">Avg</th>
                <th className="num">Last</th>
                <th className="num">Market value</th>
                <th className="num">Unrealized</th>
                <th>Strategies</th>
              </tr>
            </thead>
            <tbody>
              {positions.data.map((position) => (
                <tr key={position.symbol}>
                  <td>
                    <Link to={`/positions/${encodeURIComponent(position.symbol)}`}>{position.symbol}</Link>
                  </td>
                  <td>{position.side}</td>
                  <td className="num">{shares(position.qty)}</td>
                  <td className="num">{money(position.avg_entry_price)}</td>
                  <td className="num">{money(position.current_price)}</td>
                  <td className="num">{money(position.market_value)}</td>
                  <td className={`num ${tone(position.unrealized_pl)}`}>{money(position.unrealized_pl)}</td>
                  <td><StrategyTags symbol={position.symbol} positions={strategyPositions.data ?? []} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
      <section className="panel activity-panel">
        <div className="panel-head"><div><h2>Recent transactions</h2><p>Fills accepted through this OMS</p></div></div>
        {fills.error ? <Banner>Recent OMS transactions are temporarily unavailable: {fills.error}</Banner> : null}
        {!fills.loading && fills.data && fills.data.length === 0 ? <Empty>No OMS fills yet.</Empty> : null}
        {fills.data && fills.data.length > 0 ? (
          <table>
            <thead><tr><th>Time</th><th>Transaction</th><th>Strategy</th><th className="num">Quantity</th><th className="num">Fill price</th></tr></thead>
            <tbody>{fills.data.map((fill) => (
              <tr key={fill.id}>
                <td>{when(fill.filled_at)}</td>
                <td><Link to={`/positions/${encodeURIComponent(fill.symbol)}`}>{fill.side} {fill.symbol}</Link></td>
                <td><Link className="strategy-tag" to={`/strategies/${encodeURIComponent(fill.strategy_id)}`}>{fill.strategy_id}</Link></td>
                <td className="num">{shares(fill.qty)}</td>
                <td className="num">{price(fill.price)}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : null}
      </section>
    </div>
  );
}

function Balance({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function StrategyTags({ symbol, positions }: { symbol: string; positions: StrategyPosition[] }) {
  const tagged = positions.filter((position) => position.symbol === symbol);
  if (tagged.length === 0) return <span className="muted">Outside OMS</span>;
  return <div className="strategy-tags">{tagged.map((position) => (
    <Link className="strategy-tag" key={position.strategy_id} to={`/strategies/${encodeURIComponent(position.strategy_id)}`}>
      {position.strategy_id} · {shares(position.qty)}
    </Link>
  ))}</div>;
}
