import { Link, useParams } from "react-router-dom";
import { useQuery, type Book, type Order } from "../api";
import { money, price, shares, tone, when } from "../format";
import { Banner, Empty, PageHeader } from "../Shell";
import { Status } from "./Status";

export function StrategyDetail() {
  const { id = "" } = useParams();
  const query = useQuery<Book>(`/api/strategies/${encodeURIComponent(id)}/book`);
  const book = query.data;

  return (
    <>
      <PageHeader title={id} lede="Orders, fills, and the positions booked from this strategy id." />
      {query.error ? <Banner>{query.error}</Banner> : null}
      {book ? (
        <section className="stats">
          <article className="stat">
            <span>Cash left</span>
            <strong>{money(book.strategy.cash_remaining)}</strong>
          </article>
          <article className="stat">
            <span>Budget</span>
            <strong>{money(book.strategy.cash_budget)}</strong>
          </article>
          <article className="stat">
            <span>Realized</span>
            <strong className={tone(book.strategy.realized_pnl)}>{money(book.strategy.realized_pnl)}</strong>
          </article>
          <article className="stat">
            <span>Unrealized</span>
            <strong className={tone(book.strategy.unrealized_pnl)}>{money(book.strategy.unrealized_pnl)}</strong>
          </article>
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-head">
          <h2>Positions</h2>
        </div>
        {book && book.positions.length === 0 ? <Empty>No open positions for this strategy.</Empty> : null}
        {book && book.positions.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="num">Qty</th>
                <th className="num">Avg</th>
                <th className="num">Unrealized</th>
              </tr>
            </thead>
            <tbody>
              {book.positions.map((position) => (
                <tr key={position.symbol}>
                  <td>
                    <Link to={`/positions/${encodeURIComponent(position.symbol)}`}>{position.symbol}</Link>
                  </td>
                  <td className="num">{shares(position.qty)}</td>
                  <td className="num">{price(position.avg_price)}</td>
                  <td className={`num ${tone(position.unrealized_pnl)}`}>{money(position.unrealized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Orders</h2>
        </div>
        {book && book.orders.length === 0 ? <Empty>No orders yet.</Empty> : null}
        {book && book.orders.length > 0 ? <OrderTable orders={book.orders} /> : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Fills</h2>
        </div>
        {book && book.fills.length === 0 ? <Empty>No fills yet.</Empty> : null}
        {book && book.fills.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Symbol</th>
                <th>Side</th>
                <th className="num">Qty</th>
                <th className="num">Price</th>
              </tr>
            </thead>
            <tbody>
              {book.fills.map((fill) => (
                <tr key={fill.id}>
                  <td>{when(fill.filled_at)}</td>
                  <td>
                    <Link to={`/positions/${encodeURIComponent(fill.symbol)}`}>{fill.symbol}</Link>
                  </td>
                  <td>{fill.side}</td>
                  <td className="num">{shares(fill.qty)}</td>
                  <td className="num">{price(fill.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </>
  );
}

export function OrderTable({ orders }: { orders: Order[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>When</th>
          <th>Order</th>
          <th>Status</th>
          <th className="num">Qty</th>
          <th className="num">Filled</th>
          <th className="num">Limit</th>
        </tr>
      </thead>
      <tbody>
        {orders.map((order) => (
          <tr key={order.id}>
            <td>{when(order.created_at)}</td>
            <td>
              <div className="order-label">{orderLabel(order)}</div>
              <div className="muted">{order.client_order_id || order.id.slice(0, 8)}</div>
            </td>
            <td>
              <Status value={order.status} />
              {order.reject_reason ? <div className="muted">{order.reject_reason}</div> : null}
            </td>
            <td className="num">{shares(order.qty)}</td>
            <td className="num">{shares(order.filled_qty)}</td>
            <td className="num">{price(order.limit_price)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function orderLabel(order: Order): string {
  if (order.order_class === "mleg") {
    return order.legs.map((leg) => `${leg.side} ${leg.symbol}`).join(" / ");
  }
  return `${order.side} ${order.symbol}`;
}
