# Simpli OMS

A thin order manager for one Alpaca paper account. External systems send finished orders. This service checks the strategy, stores the order, and submits it to Alpaca. Alpaca performs the execution. The dashboard reads the shared paper account from Alpaca and reads each strategy's orders, fills, and positions from this service.

Signals and trade ideas stay outside. Every order names a strategy id that was registered here first.

## Paper account

1. Create a free Alpaca account at [alpaca.markets](https://alpaca.markets/).
2. Open the dashboard at [app.alpaca.markets](https://app.alpaca.markets/) and switch to the paper account. A new account has a paper account already.
3. Generate a paper API key and secret. These are separate from live keys. Do not put live keys in this project.
4. Paper accounts already have options Level 3, including multi-leg orders. No extra approval is required for paper.

## Run it

Python 3.11 or newer:

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env`:

```bash
APCA_API_KEY_ID=your-paper-key
APCA_API_SECRET_KEY=your-paper-secret
APCA_API_BASE_URL=https://paper-api.alpaca.markets
OMS_API_KEY=dev-key
```

`OMS_API_KEY` is the single credential for this platform. Callers send it as `X-API-Key`. The Alpaca secret stays on the server.

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

## Cash in this version

A limit buy, a debit option, or a debit multi-leg order is rejected when `limit price × quantity × multiplier` is above the strategy's remaining cash. The multiplier is 1 for stocks and 100 for options. Cash and positions change when Alpaca reports a fill. Two orders sent at the same moment can both pass the cash check; reserving cash at accept time is later work. `sell_to_open` is forwarded to Alpaca without an options-margin model.
