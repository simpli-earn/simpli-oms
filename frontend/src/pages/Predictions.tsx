import { FormEvent, useEffect, useState } from "react";
import {
  api,
  useQuery,
  type KalshiOrder,
  type KalshiPortfolio,
  type PaperAccount,
  type PaperBook,
  type PaperOrder,
  type PredictionMarket,
  type PredictionStatus,
  type Strategy,
} from "../api";
import { money, shares, tone, when } from "../format";
import { Banner, Empty, PageHeader } from "../Shell";

type Venue = "kalshi" | "kalshi-demo" | "polymarket";
type Tab = Venue | "mentions";

const TABS: Array<[Tab, string]> = [
  ["kalshi", "Kalshi"],
  ["mentions", "Kalshi mentions"],
  ["polymarket", "Polymarket"],
  ["kalshi-demo", "Kalshi demo"],
];

const LEDES: Record<Tab, string> = {
  kalshi: "Live Kalshi markets. Paper orders fill against the live book; no real money is used.",
  mentions: "Kalshi's \"What will X say\" markets, refreshed every 10 minutes. Paper orders fill against the live book.",
  polymarket: "Live Polymarket markets. Paper orders fill against the live book; no real money is used.",
  "kalshi-demo": "Kalshi's own demo exchange, with demo funds. Needs Kalshi demo API keys.",
};

const venueOf = (tab: Tab): Venue => (tab === "mentions" ? "kalshi" : tab);

export function cents(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}¢`;
}

function volume(value: number | null | undefined, venue: Tab): string {
  if (value == null) return "—";
  const short = value.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
  return venue === "polymarket" ? `$${short}` : short;
}

export function Predictions() {
  const [tab, setTab] = useState<Tab>("kalshi");
  const venue = venueOf(tab);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const status = useQuery<PredictionStatus>("/api/predictions/status");
  const markets = useQuery<PredictionMarket[]>(
    `/api/predictions/markets?venue=${venue}&limit=60&q=${encodeURIComponent(q)}${tab === "mentions" ? "&category=mentions" : ""}`,
  );

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(search.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [search]);

  const demoReady = status.data?.kalshi_demo_configured ?? false;
  const paperReady = status.data?.paper_ready ?? false;
  const scan = status.data?.mentions_scan;

  return (
    <>
      <PageHeader title="Predictions" lede={LEDES[tab]}>
        <span className="paper-pill">Paper</span>
      </PageHeader>
      {status.data && !paperReady ? (
        <Banner>
          Paper trading is not set up in the shared database yet. A Supabase admin needs to run
          backend/supabase_predictions.sql once. Browsing works now.
        </Banner>
      ) : null}
      {paperReady ? <PaperPanel /> : null}
      <div className="predictions-bar">
        <div className="segmented" role="tablist">
          {TABS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={tab === value ? "selected" : ""}
              onClick={() => {
                setTab(value);
                setSelected(null);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search markets"
          aria-label="Search markets"
        />
      </div>

      {tab === "mentions" && scan?.running ? (
        <p className="muted scan-note">Scanning Kalshi mention series: {scan.done} of {scan.total || "…"}. The list fills in as it goes.</p>
      ) : null}
      {venue === "kalshi-demo" && status.data && !demoReady ? (
        <Banner>
          Browsing works without keys. To place demo orders, create an API key at demo.kalshi.co and add
          KALSHI_DEMO_API_KEY_ID and KALSHI_DEMO_PRIVATE_KEY_PATH to backend/.env, then restart the OMS.
        </Banner>
      ) : null}
      {venue === "kalshi-demo" && demoReady ? <KalshiPortfolioPanel /> : null}

      <div className={`predictions-grid${selected ? " with-detail" : ""}`}>
        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Markets</h2>
              <p>Busiest first. Prices are the YES side, in cents per $1 contract.</p>
            </div>
            <span className="muted">{markets.data?.length ?? 0} shown</span>
          </div>
          {markets.error ? <Banner>{markets.error}</Banner> : null}
          {!markets.loading && markets.data?.length === 0 ? <Empty>No open markets match.</Empty> : null}
          {markets.data && markets.data.length > 0 ? (
            <table className="markets-table">
              <thead>
                <tr>
                  <th>Market</th>
                  <th className="num">Bid</th>
                  <th className="num">Ask</th>
                  <th className="num">Last</th>
                  <th className="num">24h vol</th>
                  <th>Closes</th>
                </tr>
              </thead>
              <tbody>
                {markets.data.map((market) => (
                  <tr
                    key={market.id}
                    className={selected === market.id ? "selected" : ""}
                    onClick={() => setSelected(market.id)}
                  >
                    <td>
                      <div className="market-title">{market.title}</div>
                      {market.subtitle ? <div className="muted market-sub">{market.subtitle}</div> : null}
                    </td>
                    <td className="num">{cents(market.yes_bid)}</td>
                    <td className="num">{cents(market.yes_ask)}</td>
                    <td className="num">{cents(market.last)}</td>
                    <td className="num">{volume(market.volume_24h, tab)}</td>
                    <td className="muted">{market.close_time ? when(market.close_time) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>
        {selected ? (
          <MarketDetail venue={venue} id={selected} demoReady={demoReady} paperReady={paperReady} onClose={() => setSelected(null)} />
        ) : null}
      </div>
    </>
  );
}

function MarketDetail({
  venue,
  id,
  demoReady,
  paperReady,
  onClose,
}: {
  venue: Venue;
  id: string;
  demoReady: boolean;
  paperReady: boolean;
  onClose: () => void;
}) {
  const market = useQuery<PredictionMarket>(`/api/predictions/markets/${venue}/${encodeURIComponent(id)}`);
  const data = market.data;

  return (
    <section className="panel market-detail">
      <div className="panel-head">
        <div>
          <h2>{data?.title ?? id}</h2>
          <p>
            {data?.subtitle ? `${data.subtitle} · ` : ""}
            {id}
          </p>
        </div>
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
      {market.error ? <Banner>{market.error}</Banner> : null}
      {data ? (
        <div className="market-body">
          <div className="balance-list">
            <div><span>YES bid</span><strong>{cents(data.yes_bid)}</strong></div>
            <div><span>YES ask</span><strong>{cents(data.yes_ask)}</strong></div>
            <div><span>Last</span><strong>{cents(data.last)}</strong></div>
            <div><span>Closes</span><strong>{data.close_time ? when(data.close_time) : "—"}</strong></div>
          </div>
          {data.outcomes && data.outcomes.length > 2 ? (
            <p className="muted">{data.outcomes.map((o) => `${o.name} ${cents(o.price)}`).join(" · ")}</p>
          ) : null}
          <OrderBook book={data.book} />
          {data.rules ? <p className="market-rules">{data.rules}</p> : null}
          {data.url ? (
            <p>
              <a href={data.url} target="_blank" rel="noreferrer">Open on {venue === "polymarket" ? "Polymarket" : "Kalshi"}</a>
            </p>
          ) : null}
          {venue === "kalshi-demo" && demoReady ? <KalshiTicket market={data} /> : null}
          {venue !== "kalshi-demo" && paperReady ? <PaperTicket venue={venue} market={data} /> : null}
        </div>
      ) : null}
    </section>
  );
}

function OrderBook({ book }: { book?: PredictionMarket["book"] }) {
  if (!book || (book.bids.length === 0 && book.asks.length === 0)) return <Empty>The order book is empty.</Empty>;
  const rows = Math.max(book.bids.length, book.asks.length);
  return (
    <table className="book-table">
      <thead>
        <tr>
          <th className="num">Bid size</th>
          <th className="num">YES bid</th>
          <th className="num">YES ask</th>
          <th className="num">Ask size</th>
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: Math.min(rows, 8) }, (_, i) => (
          <tr key={i}>
            <td className="num muted">{book.bids[i] ? shares(book.bids[i][1]) : ""}</td>
            <td className="num up">{book.bids[i] ? cents(book.bids[i][0]) : ""}</td>
            <td className="num down">{book.asks[i] ? cents(book.asks[i][0]) : ""}</td>
            <td className="num muted">{book.asks[i] ? shares(book.asks[i][1]) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function KalshiTicket({ market }: { market: PredictionMarket }) {
  const [side, setSide] = useState<"yes" | "no">("yes");
  const [action, setAction] = useState<"buy" | "sell">("buy");
  const [count, setCount] = useState("1");
  const [price, setPrice] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<KalshiOrder | null>(null);
  const priceDollars = Number(price) / 100;
  const cost = Number(count) * priceDollars;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const order = await api<KalshiOrder>("/api/predictions/kalshi/orders", {
        method: "POST",
        body: JSON.stringify({ ticker: market.id, side, action, count: Number(count), price: priceDollars }),
      });
      setResult(order);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Order failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="ticket" onSubmit={onSubmit}>
      <div className="fields">
        <label>
          Side
          <select value={side} onChange={(event) => setSide(event.target.value as "yes" | "no")}>
            <option value="yes">YES</option>
            <option value="no">NO</option>
          </select>
        </label>
        <label>
          Action
          <select value={action} onChange={(event) => setAction(event.target.value as "buy" | "sell")}>
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </label>
        <label>
          Contracts
          <input value={count} onChange={(event) => setCount(event.target.value)} inputMode="numeric" required />
        </label>
        <label>
          Limit (¢)
          <input
            value={price}
            onChange={(event) => setPrice(event.target.value)}
            inputMode="numeric"
            required
            placeholder={side === "yes" ? String(Math.round((market.yes_ask ?? 0.5) * 100)) : String(Math.round((1 - (market.yes_bid ?? 0.5)) * 100))}
          />
        </label>
      </div>
      <div className="ticket-foot">
        <p>{cost > 0 ? `${action === "buy" ? "Costs" : "Receives"} ${money(cost)}, pays ${money(Number(count))} if ${side.toUpperCase()}` : "Limit order on the Kalshi demo exchange."}</p>
        <button type="submit" disabled={pending}>{pending ? "Sending…" : "Send demo order"}</button>
      </div>
      {error ? <Banner>{error}</Banner> : null}
      {result ? <p className="muted">Order {result.order_id} · {result.status}</p> : null}
    </form>
  );
}

function KalshiPortfolioPanel() {
  const portfolio = useQuery<KalshiPortfolio>("/api/predictions/kalshi/portfolio");
  const [error, setError] = useState<string | null>(null);
  const data = portfolio.data;
  const open = data?.orders.filter((order) => order.status === "resting") ?? [];

  async function cancel(id: string) {
    setError(null);
    try {
      await api(`/api/predictions/kalshi/orders/${encodeURIComponent(id)}`, { method: "DELETE" });
      await portfolio.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel failed");
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div><h2>Kalshi demo account</h2><p>Balance, positions, and resting orders on the paper exchange</p></div>
        <span className="paper-pill">Paper</span>
      </div>
      {portfolio.error ? <Banner>{portfolio.error}</Banner> : null}
      {error ? <Banner>{error}</Banner> : null}
      {data ? (
        <>
          <div className="balance-list">
            <div><span>Cash</span><strong>{money(data.balance)}</strong></div>
            <div><span>Positions value</span><strong>{money(data.portfolio_value)}</strong></div>
          </div>
          {data.positions.length === 0 ? <Empty>No open demo positions.</Empty> : (
            <table>
              <thead><tr><th>Market</th><th className="num">Contracts</th><th className="num">Exposure</th><th className="num">Realized</th></tr></thead>
              <tbody>{data.positions.map((p) => (
                <tr key={p.ticker}>
                  <td>{p.ticker}</td>
                  <td className="num">{p.position != null && p.position < 0 ? `${shares(-p.position)} NO` : `${shares(p.position)} YES`}</td>
                  <td className="num">{money(p.exposure)}</td>
                  <td className="num">{money(p.realized_pnl)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
          {open.length > 0 ? (
            <table>
              <thead><tr><th>Resting order</th><th>Side</th><th className="num">Price</th><th className="num">Left</th><th /></tr></thead>
              <tbody>{open.map((o) => (
                <tr key={o.order_id}>
                  <td>{o.ticker}</td>
                  <td>{o.action} {o.side?.toUpperCase()}</td>
                  <td className="num">{cents(o.side === "no" ? o.no_price : o.yes_price)}</td>
                  <td className="num">{shares(o.remaining)}</td>
                  <td className="num"><button type="button" className="secondary" onClick={() => void cancel(o.order_id)}>Cancel</button></td>
                </tr>
              ))}</tbody>
            </table>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function PaperPanel() {
  const accounts = useQuery<PaperAccount[]>("/api/predictions/paper/accounts");
  const strategies = useQuery<Strategy[]>("/api/strategies");
  const [selected, setSelected] = useState<string | null>(null);
  const [fundId, setFundId] = useState("");
  const [amount, setAmount] = useState("1000");
  const [error, setError] = useState<string | null>(null);
  const funded = new Set((accounts.data ?? []).map((a) => a.strategy_id));
  const unfunded = (strategies.data ?? []).filter((s) => !funded.has(s.id));
  const current = selected ?? accounts.data?.[0]?.strategy_id ?? null;

  async function onFund(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api("/api/predictions/paper/accounts", {
        method: "POST",
        body: JSON.stringify({ strategy_id: fundId || unfunded[0]?.id, cash_budget: Number(amount) }),
      });
      await accounts.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Funding failed");
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div><h2>Paper accounts</h2><p>Prediction-market cash per strategy, separate from its Alpaca budget</p></div>
      </div>
      {accounts.error ? <Banner>{accounts.error}</Banner> : null}
      {error ? <Banner>{error}</Banner> : null}
      {accounts.data && accounts.data.length > 0 ? (
        <table>
          <thead><tr><th>Strategy</th><th className="num">Equity</th><th className="num">Cash</th><th className="num">In orders</th><th className="num">Positions</th><th className="num">Total P&amp;L</th><th className="num">Fees</th></tr></thead>
          <tbody>{accounts.data.map((a) => (
            <tr key={a.strategy_id} className={a.strategy_id === current ? "selected" : ""} onClick={() => setSelected(a.strategy_id)} style={{ cursor: "pointer" }}>
              <td><span className="strategy-tag">{a.strategy_id}</span></td>
              <td className="num">{money(a.equity)}</td>
              <td className="num">{money(a.cash)}</td>
              <td className="num">{money(a.cash_reserved)}</td>
              <td className="num">{a.open_positions}</td>
              <td className={`num ${tone(a.total_pnl)}`}>{money(a.total_pnl)}</td>
              <td className="num">{money(a.fees_paid)}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : accounts.data ? <Empty>No strategy has prediction paper cash yet. Fund one below.</Empty> : null}
      {unfunded.length > 0 ? (
        <form className="inline-form paper-fund" onSubmit={onFund}>
          <label>Strategy<select value={fundId || unfunded[0]?.id} onChange={(e) => setFundId(e.target.value)}>
            {unfunded.map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
          </select></label>
          <label>Paper cash<input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" /></label>
          <button type="submit">Fund</button>
        </form>
      ) : null}
      {current ? <PaperBookView strategyId={current} /> : null}
    </section>
  );
}

function PaperBookView({ strategyId }: { strategyId: string }) {
  const book = useQuery<PaperBook>(`/api/predictions/paper/accounts/${encodeURIComponent(strategyId)}`);
  const [error, setError] = useState<string | null>(null);
  const open = book.data?.orders.filter((o) => o.status === "open") ?? [];
  const recent = book.data?.orders.filter((o) => o.status !== "open").slice(0, 8) ?? [];

  async function cancel(id: string) {
    setError(null);
    try {
      await api(`/api/predictions/paper/orders/${encodeURIComponent(id)}`, { method: "DELETE" });
      await book.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel failed");
    }
  }

  if (!book.data) return book.error ? <Banner>{book.error}</Banner> : null;
  return (
    <div className="paper-book">
      {error ? <Banner>{error}</Banner> : null}
      <h3>{strategyId} positions</h3>
      {book.data.positions.length === 0 ? <Empty>No open positions.</Empty> : (
        <table>
          <thead><tr><th>Market</th><th>Side</th><th className="num">Contracts</th><th className="num">Avg</th><th className="num">Mark</th><th className="num">Value</th><th className="num">Unrealized</th></tr></thead>
          <tbody>{book.data.positions.map((p) => (
            <tr key={`${p.venue}-${p.market_id}-${p.outcome}`}>
              <td><div className="market-title">{p.title || p.market_id}</div><div className="muted market-sub">{p.venue} · {p.market_id}</div></td>
              <td>{p.outcome.toUpperCase()}</td>
              <td className="num">{shares(p.count)}</td>
              <td className="num">{cents(p.avg_price)}</td>
              <td className="num">{cents(p.mark)}</td>
              <td className="num">{money(p.market_value)}</td>
              <td className={`num ${tone(p.unrealized_pnl)}`}>{money(p.unrealized_pnl)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {open.length > 0 ? <OrderTable title="Resting orders" orders={open} onCancel={cancel} /> : null}
      {recent.length > 0 ? <OrderTable title="Recent orders" orders={recent} /> : null}
    </div>
  );
}

function OrderTable({ title, orders, onCancel }: { title: string; orders: PaperOrder[]; onCancel?: (id: string) => void }) {
  return (
    <>
      <h3>{title}</h3>
      <table>
        <thead><tr><th>Market</th><th>Order</th><th className="num">Filled</th><th className="num">Avg</th><th>Status</th><th /></tr></thead>
        <tbody>{orders.map((o) => (
          <tr key={o.id}>
            <td><div className="market-title">{o.title || o.market_id}</div><div className="muted market-sub">{when(o.created_at)}</div></td>
            <td>{o.action} {shares(o.count)} {o.outcome.toUpperCase()} {o.limit_price != null ? `@ ${cents(o.limit_price)}` : "market"}</td>
            <td className="num">{shares(o.filled_count)}</td>
            <td className="num">{cents(o.avg_price)}</td>
            <td title={o.reject_reason ?? ""}>{o.status}{o.reject_reason ? <div className="muted market-sub">{o.reject_reason}</div> : null}</td>
            <td className="num">{onCancel ? <button type="button" className="secondary" onClick={() => onCancel(o.id)}>Cancel</button> : null}</td>
          </tr>
        ))}</tbody>
      </table>
    </>
  );
}

function PaperTicket({ venue, market }: { venue: Venue; market: PredictionMarket }) {
  const accounts = useQuery<PaperAccount[]>("/api/predictions/paper/accounts");
  const [strategyId, setStrategyId] = useState("");
  const [outcome, setOutcome] = useState<"yes" | "no">("yes");
  const [action, setAction] = useState<"buy" | "sell">("buy");
  const [count, setCount] = useState("10");
  const [limit, setLimit] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PaperOrder | null>(null);
  const sid = strategyId || accounts.data?.[0]?.strategy_id || "";
  const quoted = outcome === "yes"
    ? (action === "buy" ? market.yes_ask : market.yes_bid)
    : (action === "buy" ? (market.yes_bid != null ? 1 - market.yes_bid : null) : (market.yes_ask != null ? 1 - market.yes_ask : null));
  const priceEach = limit ? Number(limit) / 100 : quoted ?? 0;
  const amount = Number(count) * priceEach;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const order = await api<PaperOrder>("/api/predictions/paper/orders", {
        method: "POST",
        body: JSON.stringify({
          strategy_id: sid, venue, market_id: market.id, outcome, action, count: Number(count),
          type: limit ? "limit" : "market", limit_price: limit ? Number(limit) / 100 : null,
        }),
      });
      setResult(order);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Order failed");
    } finally {
      setPending(false);
    }
  }

  if (accounts.data && accounts.data.length === 0) {
    return <p className="muted">Fund a strategy in Paper accounts above to place paper orders.</p>;
  }
  return (
    <form className="ticket" onSubmit={onSubmit}>
      <div className="fields">
        <label>Strategy<select value={sid} onChange={(e) => setStrategyId(e.target.value)}>
          {(accounts.data ?? []).map((a) => <option key={a.strategy_id} value={a.strategy_id}>{a.strategy_id} · {money(a.cash)}</option>)}
        </select></label>
        <label>Outcome<select value={outcome} onChange={(e) => setOutcome(e.target.value as "yes" | "no")}>
          <option value="yes">YES</option><option value="no">NO</option>
        </select></label>
        <label>Action<select value={action} onChange={(e) => setAction(e.target.value as "buy" | "sell")}>
          <option value="buy">Buy</option><option value="sell">Sell</option>
        </select></label>
        <label>Contracts<input value={count} onChange={(e) => setCount(e.target.value)} inputMode="decimal" required /></label>
        <label>Limit (¢)<input value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" placeholder="blank = market" /></label>
      </div>
      <div className="ticket-foot">
        <p>{amount > 0 ? `About ${money(amount)} at ${cents(priceEach)}, pays ${money(Number(count))} if ${outcome.toUpperCase()}` : "Fills against the live order book."}</p>
        <button type="submit" disabled={pending || !sid}>{pending ? "Sending…" : "Paper trade"}</button>
      </div>
      {error ? <Banner>{error}</Banner> : null}
      {result ? (
        <p className="muted">
          {result.status === "rejected" || result.status === "canceled"
            ? `${result.status}: ${result.reject_reason}`
            : `${result.status} · ${shares(result.filled_count)} of ${shares(result.count)} filled${result.avg_price != null ? ` at ${cents(result.avg_price)}` : ""}`}
        </p>
      ) : null}
    </form>
  );
}
