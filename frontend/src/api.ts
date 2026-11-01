import { useCallback, useEffect, useState } from "react";

export type Strategy = {
  id: string;
  cash_budget: number;
  cash_remaining: number;
  cash_reserved: number;
  realized_pnl: number;
  unrealized_pnl: number | null;
  created_at: string;
};

export type Leg = {
  symbol: string;
  ratio_qty: number;
  side: string;
  position_intent: string;
  filled_qty: number;
  filled_avg_price: number | null;
};

export type Order = {
  id: string;
  strategy_id: string;
  client_order_id: string | null;
  alpaca_order_id: string | null;
  asset_class: string;
  order_class: string;
  symbol: string | null;
  side: string | null;
  type: string;
  qty: number;
  limit_price: number | null;
  stop_price: number | null;
  time_in_force: string;
  position_intent: string | null;
  status: string;
  filled_qty: number;
  filled_avg_price: number | null;
  reject_reason: string | null;
  legs: Leg[];
  created_at: string;
  updated_at: string;
};

export type Fill = {
  id: number;
  order_id: string;
  strategy_id: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  filled_at: string;
};

export type StrategyPosition = {
  strategy_id: string;
  symbol: string;
  asset_class: string;
  qty: number;
  avg_price: number;
  unrealized_pnl: number | null;
};

export type Account = {
  id: string;
  status: string;
  currency: string;
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
};

export type AccountPosition = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  market_value: number;
  unrealized_pl: number;
  current_price: number;
  side: string;
  asset_class: string;
};

export type Book = {
  strategy: Strategy;
  positions: StrategyPosition[];
  orders: Order[];
  fills: Fill[];
};

export type SymbolDetail = {
  symbol: string;
  account_position: AccountPosition | null;
  account_error: string | null;
  strategies: Array<{
    strategy_id: string;
    qty: number;
    avg_price: number;
    asset_class: string;
    trades: Fill[];
  }>;
};

export type PredictionMarket = {
  venue: string;
  id: string;
  title: string;
  subtitle: string;
  event: string;
  category: string;
  yes_bid: number | null;
  yes_ask: number | null;
  last: number | null;
  volume_24h: number | null;
  volume: number | null;
  close_time: string | null;
  status: string | null;
  url: string | null;
  tradable: boolean;
  outcomes?: Array<{ name: string; price: number | null }>;
  rules?: string;
  book?: { bids: Array<[number, number]>; asks: Array<[number, number]> };
};

export type PredictionStatus = {
  venues: Array<{ id: string; name: string; env: string; tradable: boolean }>;
  kalshi_demo_configured: boolean;
  paper_ready: boolean;
  mentions_scan: { done: number; total: number; running: boolean };
};

export type PaperAccount = {
  strategy_id: string;
  cash_budget: number;
  cash: number;
  cash_reserved: number;
  positions_value: number;
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  fees_paid: number;
  total_pnl: number;
  open_positions: number;
  created_at: string;
};

export type PaperPosition = {
  strategy_id: string;
  venue: string;
  market_id: string;
  outcome: string;
  title: string;
  count: number;
  avg_price: number;
  mark: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  realized_pnl: number;
};

export type PaperOrder = {
  id: string;
  strategy_id: string;
  client_order_id: string | null;
  venue: string;
  market_id: string;
  title: string;
  outcome: string;
  action: string;
  type: string;
  count: number;
  limit_price: number | null;
  status: string;
  filled_count: number;
  avg_price: number | null;
  fees: number;
  reject_reason: string | null;
  created_at: string;
};

export type PaperBook = {
  account: PaperAccount;
  positions: PaperPosition[];
  orders: PaperOrder[];
};

export type KalshiOrder = {
  order_id: string;
  client_order_id: string | null;
  ticker: string;
  side: string | null;
  action: string | null;
  type: string | null;
  status: string;
  yes_price: number | null;
  no_price: number | null;
  count: number | null;
  filled: number | null;
  remaining: number | null;
  created_time: string | null;
};

export type KalshiPortfolio = {
  balance: number | null;
  portfolio_value: number | null;
  positions: Array<{
    ticker: string;
    position: number | null;
    exposure: number | null;
    realized_pnl: number | null;
    fees_paid: number | null;
    traded: number | null;
  }>;
  orders: KalshiOrder[];
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-API-Key", sessionStorage.getItem("oms_api_key") ?? "");
  if (init?.body) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) {
    window.dispatchEvent(new Event("oms-unauthorized"));
    throw new ApiError(401, "invalid api key");
  }
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = formatDetail(body.detail) || message;
    } catch {
      // The body was not JSON.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return "";
  return detail
    .map((item) => {
      if (!item || typeof item !== "object" || !("msg" in item)) return JSON.stringify(item);
      const record = item as { msg: string; loc?: unknown[] };
      const loc = Array.isArray(record.loc) ? record.loc.filter((part) => part !== "body").join(".") : "";
      return loc ? `${loc}: ${record.msg}` : record.msg;
    })
    .join(" ");
}

export function useQuery<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      const next = await api<T>(path);
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => void reload(), 5000);
    return () => window.clearInterval(timer);
  }, [reload]);

  return { data, error, loading, reload };
}
