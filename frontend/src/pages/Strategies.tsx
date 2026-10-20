import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, useQuery, type Strategy } from "../api";
import { money, tone } from "../format";
import { Banner, Empty, PageHeader } from "../Shell";

export function Strategies() {
  const query = useQuery<Strategy[]>("/api/strategies");
  const [id, setId] = useState("");
  const [budget, setBudget] = useState("100000");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await api("/api/strategies", {
        method: "POST",
        body: JSON.stringify({ id, cash_budget: Number(budget) }),
      });
      setId("");
      await query.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the strategy");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Strategies"
        lede="Register a strategy before it can trade. Its id is required on every order from the external platform."
      />
      <form className="inline-form" onSubmit={onSubmit}>
        <label>
          Strategy id
          <input
            value={id}
            onChange={(event) => setId(event.target.value)}
            placeholder="ritwij-test-straddle-volatility"
            required
          />
        </label>
        <label>
          Cash budget
          <input
            value={budget}
            onChange={(event) => setBudget(event.target.value)}
            inputMode="decimal"
            required
          />
        </label>
        <button type="submit" disabled={pending}>
          {pending ? "Saving…" : "Register"}
        </button>
      </form>
      {error ? <Banner>{error}</Banner> : null}
      <section className="panel">
        {query.error ? <Banner>{query.error}</Banner> : null}
        {!query.loading && query.data && query.data.length === 0 ? (
          <Empty>No strategies yet.</Empty>
        ) : null}
        {query.data && query.data.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th className="num">Budget</th>
                <th className="num">Cash left</th>
                <th className="num">Realized</th>
                <th className="num">Unrealized</th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((strategy) => (
                <tr key={strategy.id}>
                  <td>
                    <Link to={`/strategies/${encodeURIComponent(strategy.id)}`}>{strategy.id}</Link>
                  </td>
                  <td className="num">{money(strategy.cash_budget)}</td>
                  <td className="num">{money(strategy.cash_remaining)}</td>
                  <td className={`num ${tone(strategy.realized_pnl)}`}>{money(strategy.realized_pnl)}</td>
                  <td className={`num ${tone(strategy.unrealized_pnl)}`}>{money(strategy.unrealized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </>
  );
}
