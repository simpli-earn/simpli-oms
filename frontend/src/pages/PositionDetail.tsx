import { Link, useParams } from "react-router-dom";
import { useQuery, type SymbolDetail } from "../api";
import { money, price, shares, tone, when } from "../format";
import { Banner, Empty, PageHeader } from "../Shell";

export function PositionDetail() {
  const { symbol = "" } = useParams();
  const query = useQuery<SymbolDetail>(`/api/positions/${encodeURIComponent(symbol)}`);
  const detail = query.data;
  const account = detail?.account_position;

  return (
    <>
      <PageHeader
        title={symbol}
        lede="The paper account's position, and the strategy trades that make it up."
      />
      {query.error ? <Banner>{query.error}</Banner> : null}
      {detail?.account_error ? <Banner>{detail.account_error}</Banner> : null}
      <section className="stats">
        <article className="stat">
          <span>Account qty</span>
          <strong>{account ? shares(account.qty) : "—"}</strong>
        </article>
        <article className="stat">
          <span>Avg entry</span>
          <strong>{account ? money(account.avg_entry_price) : "—"}</strong>
        </article>
        <article className="stat">
          <span>Last</span>
          <strong>{account ? money(account.current_price) : "—"}</strong>
        </article>
        <article className="stat">
          <span>Unrealized</span>
          <strong className={tone(account?.unrealized_pl)}>{account ? money(account.unrealized_pl) : "—"}</strong>
        </article>
      </section>
      {detail && !account && detail.strategies.length === 0 ? (
        <section className="panel">
          <Empty>No open account position or OMS activity for {symbol}.</Empty>
        </section>
      ) : null}
      {detail && account && detail.strategies.length === 0 ? (
        <section className="panel">
          <Empty>No strategy has a recorded trade in this symbol.</Empty>
        </section>
      ) : null}
      {detail?.strategies.map((group) => (
        <section className="panel" key={group.strategy_id}>
          <div className="panel-head">
            <h2>
              <Link to={`/strategies/${encodeURIComponent(group.strategy_id)}`}>{group.strategy_id}</Link>
            </h2>
            <p>
              {shares(group.qty)} @ {price(group.avg_price)}
            </p>
          </div>
          {group.trades.length === 0 ? <Empty>No fills recorded.</Empty> : null}
          {group.trades.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Price</th>
                </tr>
              </thead>
              <tbody>
                {group.trades.map((trade) => (
                  <tr key={trade.id}>
                    <td>{when(trade.filled_at)}</td>
                    <td>{trade.side}</td>
                    <td className="num">{shares(trade.qty)}</td>
                    <td className="num">{price(trade.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>
      ))}
    </>
  );
}
