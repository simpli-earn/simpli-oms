import { Link } from "react-router-dom";
import { useQuery, type Account, type AccountPosition } from "../api";
import { money, shares, tone } from "../format";
import { Banner, Empty, PageHeader } from "../Shell";

export function Account() {
  const account = useQuery<Account>("/api/account");
  const positions = useQuery<AccountPosition[]>("/api/positions");

  return (
    <>
      <PageHeader
        title="Account"
        lede="The shared Alpaca paper portfolio. Strategy books are tracked separately from the orders this desk accepted."
      />
      {account.error ? <Banner>{account.error}</Banner> : null}
      <section className="stats">
        <Stat label="Equity" value={money(account.data?.equity)} />
        <Stat label="Cash" value={money(account.data?.cash)} />
        <Stat label="Buying power" value={money(account.data?.buying_power)} />
        <Stat label="Status" value={account.data?.status || (account.loading ? "…" : "—")} />
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>Positions</h2>
        </div>
        {positions.error ? <Banner>{positions.error}</Banner> : null}
        {!positions.loading && positions.data && positions.data.length === 0 ? (
          <Empty>No open positions in the paper account.</Empty>
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
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <article className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
