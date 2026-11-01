# Simpli OMS

A thin order manager for one Alpaca paper account. External systems send finished orders. This service checks the strategy, stores the order, and submits it to Alpaca. Alpaca performs the execution. The dashboard reads the shared paper account from Alpaca and reads each strategy's orders, fills, and positions from this service.

Signals and trade ideas stay outside. Every order names a strategy id that was registered here first.

## Paper account

1. Create a free Alpaca account at [alpaca.markets](https://alpaca.markets/).
2. Open the dashboard at [app.alpaca.markets](https://app.alpaca.markets/) and switch to the paper account. A new account has a paper account already.
3. Generate a paper API key and secret. These are separate from live keys. Do not put live keys in this project.
4. Paper accounts already have options Level 3, including multi-leg orders. No extra approval is required for paper.

## Quick install (Mac)

```bash
git clone -b desktop-oms https://github.com/simpli-earn/simpli-oms.git
```

Then double-click `install.command` in the `simpli-oms` folder, or run `./install.command`. When it asks, drag in the shared env file. The installer checks Python and Node (it offers to install them with Homebrew if they are missing), sets up the backend, the dashboard, and the strategies environment, builds `~/Applications/SimpliEarn Desk.app`, and opens it. Click **Start OMS** in the app. Running it again is safe.

If macOS blocks the double-click ("cannot be opened"), right-click `install.command` → **Open** once.

## Run it

Python 3.11 or newer, and Node.js 18 or newer:

```bash
python setup.py
```

The shared Alpaca paper account and the shared database are already set up. Replace `backend/.env` with the env file you were given. That file is the only thing a new checkout needs. It is not in git. These values are already filled in:

- `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` — the shared Alpaca paper keys
- `OMS_API_KEY` — callers send this as `X-API-Key`
- `OMS_DATABASE_URL` — the session-pooler connection for the `oms_app` database user

Leave the sync, pool, and CORS lines as they are in [backend/.env.example](backend/.env.example).

## Shared database

Every local backend reads and writes the same strategies, orders, fills, positions, and ledger in one Supabase Postgres database. The frontend talks only to this API. It does not use a Supabase key.

`OMS_DATABASE_URL` is the session pooler on port 5432. The app uses psycopg 3, requires TLS, and keeps a pool of two connections. `oms_app` can read and write only the OMS tables. Each order locks its strategy row while a fill is applied, so two laptops can sync at the same time. Every order still requires an existing `strategy_id`.

[`backend/supabase_schema.sql`](backend/supabase_schema.sql) is the table definition and is already applied. [`backend/import_sqlite.py`](backend/import_sqlite.py) was used once to copy the old local book. A new checkout does not run either one.

For a single-machine offline test only, SQLite remains supported by explicitly setting:

```bash
OMS_DATABASE_URL=sqlite:///./data/oms.db
```

```bash
uvicorn app.main:app --reload --port 8787
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api` to `http://127.0.0.1:8787`. Set `OMS_BACKEND_URL` to use another backend address.

Open [http://localhost:5173](http://localhost:5173) and enter the OMS API key. Interactive request schemas are at [http://localhost:8787/docs](http://localhost:8787/docs).

## How an order arrives

An external platform sends `POST /api/orders` to this service. FastAPI checks the API key, requires a registered `strategy_id`, rejects a priced debit the strategy cannot afford, and rejects a close larger than that strategy's own position. It then calls Alpaca's paper Trading API (`POST /v2/orders`) and stores the Alpaca order id. A background loop polls open orders and updates status, fills, strategy cash, and strategy positions.

Register a strategy:

```bash
curl -s -X POST http://localhost:8787/api/strategies \
  -H "X-API-Key: dev-key" \
  -H "Content-Type: application/json" \
  -d '{"id":"ritwij-test-straddle-volatility","cash_budget":100000}'
```

Equity limit:

```bash
curl -s -X POST http://localhost:8787/api/orders \
  -H "X-API-Key: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": "ritwij-test-straddle-volatility",
    "client_order_id": "idea-1001",
    "symbol": "AAPL",
    "side": "buy",
    "type": "limit",
    "qty": 1,
    "limit_price": 100,
    "time_in_force": "day"
  }'
```

Multi-leg option (a positive limit is a debit, a negative limit is a credit):

```bash
curl -s -X POST http://localhost:8787/api/orders \
  -H "X-API-Key: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": "ritwij-test-straddle-volatility",
    "client_order_id": "idea-1002",
    "order_class": "mleg",
    "type": "limit",
    "qty": 1,
    "limit_price": 0.6,
    "time_in_force": "day",
    "legs": [
      {"symbol": "AAPL250117C00150000", "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"},
      {"symbol": "AAPL250117P00150000", "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"}
    ]
  }'
```

Repeating the same `client_order_id` for a strategy returns the original order instead of sending a second one. Use a new id to retry a rejected order.

Other reads:

- `GET /api/account` and `GET /api/positions` — the shared paper account
- `GET /api/strategies/{id}/book` — that strategy's cash, positions, orders, and fills
- `GET /api/positions/{symbol}` — the account position plus each strategy's trades in that symbol
- `DELETE /api/orders/{id}` — cancel

## Prediction markets

The **Predictions** tab (web dashboard and SimpliEarn Desk) lists open Kalshi and Polymarket markets, busiest first, with each market's YES order book. **Kalshi mentions** lists Kalshi's "What will X say" markets; the backend scans Kalshi's Mentions series in the background every 10 minutes.

**Paper trading (no real money).** Nothing is sent to Kalshi or Polymarket. An order fills against the venue's live order book at the moment it is placed, the way a taker order would; a limit order's remainder rests and fills when the live book trades through its price (checked every 10 seconds). Positions pay $1 per winning contract when the market resolves. Kalshi fills are whole contracts and pay Kalshi's taker fee (7% x contracts x price x (1 - price), rounded up to the cent); resting fills and Polymarket fills are free. Each strategy gets prediction cash separate from its Alpaca budget: fund it in the tab, or with `PredictionMarkets().fund(...)`.

The paper book lives in four tables in the shared database (`pm_accounts`, `pm_orders`, `pm_fills`, `pm_positions`). `oms_app` cannot create tables, so a Supabase admin runs [`backend/supabase_predictions.sql`](backend/supabase_predictions.sql) once in the SQL editor. Until then the tab shows a banner and browsing still works.

Strategies use `PredictionMarkets` from `oms_client` (see its docstring). [`strategies/templates/kalshi_mentions.py`](strategies/templates/kalshi_mentions.py) is a starter mention-market strategy and [`poly_favorites.py`](strategies/templates/poly_favorites.py) a Polymarket one.

**Kalshi demo** is Kalshi's own demo exchange. It needs `KALSHI_DEMO_API_KEY_ID` and `KALSHI_DEMO_PRIVATE_KEY_PATH` in `backend/.env` (keys from [demo.kalshi.co](https://demo.kalshi.co)); its markets are thin, so the paper engine above is usually the better test.

Endpoints: `GET /api/predictions/markets?venue=kalshi|polymarket|kalshi-demo&q=&category=mentions`, `GET /api/predictions/markets/{venue}/{id}`, `GET|POST /api/predictions/paper/accounts`, `POST /api/predictions/paper/accounts/{id}/deposit`, `GET /api/predictions/paper/accounts/{id}`, `GET|POST /api/predictions/paper/orders`, `DELETE /api/predictions/paper/orders/{id}`, and `/api/predictions/kalshi/*` for the demo exchange.

## Cash in this version

A limit buy, a notional market buy, a debit option, or a debit multi-leg order is rejected when its estimated cost is above the strategy's spendable cash. Accepting the order reserves that cost immediately, so a second order cannot spend it. The reservation is released on reject, cancel, or expiry, and adjusted when the fill price differs from the estimate. Sells and credit multi-leg orders change cash when Alpaca reports the fill. The multiplier is 1 for stocks and 100 for options. `sell_to_open` is forwarded to Alpaca without an options-margin model.
