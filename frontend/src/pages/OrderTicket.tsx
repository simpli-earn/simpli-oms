import { FormEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, useQuery, type Order, type Strategy } from "../api";
import { money } from "../format";
import { Banner, PageHeader } from "../Shell";
import { Status } from "./Status";

type Mode = "equity" | "option" | "mleg";
type Intent = "buy_to_open" | "buy_to_close" | "sell_to_open" | "sell_to_close";

type LegForm = {
  symbol: string;
  ratio: string;
  intent: Intent;
};

const INTENTS: Array<{ value: Intent; label: string }> = [
  { value: "buy_to_open", label: "Buy to open" },
  { value: "buy_to_close", label: "Buy to close" },
  { value: "sell_to_open", label: "Sell to open" },
  { value: "sell_to_close", label: "Sell to close" },
];

function sideFor(intent: Intent): "buy" | "sell" {
  return intent.startsWith("buy") ? "buy" : "sell";
}

export function OrderTicket() {
  const strategies = useQuery<Strategy[]>("/api/strategies");
  const [mode, setMode] = useState<Mode>("equity");
  const [strategyId, setStrategyId] = useState("");
  const [clientOrderId, setClientOrderId] = useState("");
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [intent, setIntent] = useState<Intent>("buy_to_open");
  const [type, setType] = useState("limit");
  const [qty, setQty] = useState("1");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [tif, setTif] = useState("day");
  const [legs, setLegs] = useState<LegForm[]>([
    { symbol: "", ratio: "1", intent: "buy_to_open" },
    { symbol: "", ratio: "1", intent: "buy_to_open" },
  ]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<Order | null>(null);

  const selectedStrategy = strategyId || strategies.data?.[0]?.id || "";
  const types = mode === "mleg" ? ["market", "limit"] : ["market", "limit", "stop", "stop_limit"];
  const orderType = types.includes(type) ? type : "limit";
  const estimate = useMemo(
    () => costEstimate({ mode, side, intent, type: orderType, qty, limitPrice }),
    [mode, side, intent, orderType, qty, limitPrice],
  );

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setResult(null);
    if (!selectedStrategy) {
      setError("Register a strategy before placing an order.");
      return;
    }
    setPending(true);
    try {
      const created = await api<Order>("/api/orders", {
        method: "POST",
        body: JSON.stringify(payload()),
      });
      setResult(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Order failed");
    } finally {
      setPending(false);
    }
  }

  function payload(): Record<string, unknown> {
    const base: Record<string, unknown> = {
      strategy_id: selectedStrategy,
      time_in_force: tif,
      qty: Number(qty),
      type: orderType,
    };
    if (clientOrderId.trim()) base.client_order_id = clientOrderId.trim();
    if (orderType === "limit" || orderType === "stop_limit") base.limit_price = Number(limitPrice);
    if (orderType === "stop" || orderType === "stop_limit") base.stop_price = Number(stopPrice);

    if (mode === "mleg") {
      return {
        ...base,
        order_class: "mleg",
        legs: legs.map((leg) => ({
          symbol: leg.symbol,
          ratio_qty: Number(leg.ratio),
          side: sideFor(leg.intent),
          position_intent: leg.intent,
        })),
      };
    }
    if (mode === "option") {
      return {
        ...base,
        symbol,
        side: sideFor(intent),
        position_intent: intent,
      };
    }
    return { ...base, symbol, side };
  }

  return (
    <>
      <PageHeader
        title="New order"
        lede="Send a finished equity, option, or multi-leg order. Alpaca executes it on the paper account."
      />
      <form className="ticket" onSubmit={onSubmit}>
        <div className="segmented" role="tablist">
          {(
            [
              ["equity", "Equity"],
              ["option", "Option"],
              ["mleg", "Multi-leg"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              className={mode === value ? "selected" : ""}
              onClick={() => setMode(value)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="fields">
          <label>
            Strategy
            <select value={selectedStrategy} onChange={(event) => setStrategyId(event.target.value)} required>
              {(strategies.data ?? []).map((strategy) => (
                <option key={strategy.id} value={strategy.id}>
                  {strategy.id} · {money(strategy.cash_remaining)} left
                </option>
              ))}
            </select>
          </label>
          <label>
            Client order id
            <input
              value={clientOrderId}
              onChange={(event) => setClientOrderId(event.target.value)}
              placeholder="optional, unique per strategy"
            />
          </label>
          {mode !== "mleg" ? (
            <label>
              Symbol
              <input value={symbol} onChange={(event) => setSymbol(event.target.value)} required placeholder={mode === "option" ? "AAPL250117C00150000" : "AAPL"} />
            </label>
          ) : null}
          {mode === "equity" ? (
            <label>
              Side
              <select value={side} onChange={(event) => setSide(event.target.value as "buy" | "sell")}>
                <option value="buy">Buy</option>
                <option value="sell">Sell</option>
              </select>
            </label>
          ) : null}
          {mode === "option" ? (
            <label>
              Intent
              <select value={intent} onChange={(event) => setIntent(event.target.value as Intent)}>
                {INTENTS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            Type
            <select value={orderType} onChange={(event) => setType(event.target.value)}>
              {types.map((item) => (
                <option key={item} value={item}>
                  {item.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label>
            Quantity
            <input value={qty} onChange={(event) => setQty(event.target.value)} inputMode="decimal" required />
          </label>
          {orderType === "limit" || orderType === "stop_limit" ? (
            <label>
              Limit price
              <input
                value={limitPrice}
                onChange={(event) => setLimitPrice(event.target.value)}
                inputMode="decimal"
                required
                placeholder={mode === "mleg" ? "0.60 debit, -0.40 credit" : "190.00"}
              />
            </label>
          ) : null}
          {orderType === "stop" || orderType === "stop_limit" ? (
            <label>
              Stop price
              <input value={stopPrice} onChange={(event) => setStopPrice(event.target.value)} inputMode="decimal" required />
            </label>
          ) : null}
          <label>
            Time in force
            <select value={tif} onChange={(event) => setTif(event.target.value)}>
              <option value="day">Day</option>
              <option value="gtc">Good til canceled</option>
            </select>
          </label>
        </div>

        {mode === "mleg" ? (
          <div className="legs">
            {legs.map((leg, index) => (
              <div className="leg" key={index}>
                <label>
                  Leg {index + 1} symbol
                  <input
                    value={leg.symbol}
                    required
                    onChange={(event) => updateLeg(index, { symbol: event.target.value })}
                  />
                </label>
                <label>
                  Ratio
                  <input
                    value={leg.ratio}
                    inputMode="decimal"
                    required
                    onChange={(event) => updateLeg(index, { ratio: event.target.value })}
                  />
                </label>
                <label>
                  Intent
                  <select
                    value={leg.intent}
                    onChange={(event) => updateLeg(index, { intent: event.target.value as Intent })}
                  >
                    {INTENTS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ))}
            <div className="leg-actions">
              <button
                type="button"
                className="secondary"
                disabled={legs.length >= 4}
                onClick={() => setLegs([...legs, { symbol: "", ratio: "1", intent: "buy_to_open" }])}
              >
                Add leg
              </button>
              <button
                type="button"
                className="secondary"
                disabled={legs.length <= 2}
                onClick={() => setLegs(legs.slice(0, -1))}
              >
                Remove leg
              </button>
            </div>
          </div>
        ) : null}

        <div className="ticket-foot">
          <p>{estimate || "Market orders are checked by Alpaca. Limit debits are checked against strategy cash."}</p>
          <button type="submit" disabled={pending || !selectedStrategy}>
            {pending ? "Submitting…" : "Submit order"}
          </button>
        </div>
      </form>
      {strategies.error ? <Banner>{strategies.error}</Banner> : null}
      {strategies.data && strategies.data.length === 0 ? (
        <Banner>
          Register a strategy on the <Link to="/strategies">strategies page</Link> first.
        </Banner>
      ) : null}
      {error ? <Banner>{error}</Banner> : null}
      {result ? (
        <section className="panel result">
          <div className="panel-head">
            <h2>Order accepted</h2>
            <Status value={result.status} />
          </div>
          <p>
            <Link to={`/strategies/${encodeURIComponent(result.strategy_id)}`}>{result.strategy_id}</Link>
            <span className="muted"> · {result.id}</span>
          </p>
          {result.alpaca_order_id ? <p className="muted">Alpaca {result.alpaca_order_id}</p> : null}
        </section>
      ) : null}
    </>
  );

  function updateLeg(index: number, patch: Partial<LegForm>) {
    setLegs(legs.map((leg, legIndex) => (legIndex === index ? { ...leg, ...patch } : leg)));
  }
}

function costEstimate(input: {
  mode: Mode;
  side: "buy" | "sell";
  intent: Intent;
  type: string;
  qty: string;
  limitPrice: string;
}): string | null {
  const qty = Number(input.qty);
  const limit = Number(input.limitPrice);
  if (!qty || Number.isNaN(limit) || input.limitPrice === "") return null;
  if (input.type !== "limit" && input.type !== "stop_limit") return null;
  if (input.mode === "mleg") {
    if (input.type !== "limit") return null;
    const cost = limit * qty * 100;
    return cost > 0 ? `Debit checked against cash: ${money(cost)}` : `Credit, no cash reserve: ${money(Math.abs(cost))}`;
  }
  const buying = input.mode === "equity" ? input.side === "buy" : input.intent.startsWith("buy");
  if (!buying || limit <= 0) return null;
  const multiplier = input.mode === "option" ? 100 : 1;
  return `Debit checked against cash: ${money(limit * qty * multiplier)}`;
}
