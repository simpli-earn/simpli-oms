import json
import os
from urllib.request import Request, urlopen

request = Request(
    f"{os.getenv('OMS_API_URL', 'http://127.0.0.1:8787').rstrip('/')}/api/orders",
    json.dumps({"strategy_id": "beta-spy", "client_order_id": "test1", "symbol": "SPY", "side": "buy", "type": "market", "notional": 100, "time_in_force": "day"}).encode(),
    {"Content-Type": "application/json", "X-API-Key": os.getenv("OMS_API_KEY", "dev-key")},
    method="POST",
)
print(urlopen(request).read().decode())
