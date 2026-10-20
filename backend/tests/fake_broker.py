from app.services.broker import BrokerError, BrokerLeg, BrokerOrder, SubmitRequest


class FakeBroker:
    def __init__(self):
        self.submitted: list[SubmitRequest] = []
        self.orders: dict[str, BrokerOrder] = {}
        self.fail_submit = False
        self._next_id = 0
        self.account = {
            "id": "paper-account",
            "status": "ACTIVE",
            "currency": "USD",
            "equity": 100000.0,
            "cash": 100000.0,
            "buying_power": 200000.0,
            "portfolio_value": 100000.0,
        }
        self.positions: list[dict] = []

    def submit(self, request: SubmitRequest) -> BrokerOrder:
        if self.fail_submit:
            raise BrokerError("alpaca down")
        self.submitted.append(request)
        self._next_id += 1
        order = BrokerOrder(
            id=f"alpaca-{self._next_id}",
            status="accepted",
            legs=[
                BrokerLeg(
                    symbol=leg.symbol,
                    side=leg.side,
                    position_intent=leg.position_intent,
                    ratio_qty=leg.ratio_qty,
                )
                for leg in request.legs
            ],
        )
        self.orders[order.id] = order
        return order

    def cancel(self, alpaca_order_id: str) -> None:
        self.orders[alpaca_order_id].status = "canceled"

    def get_order(self, alpaca_order_id: str) -> BrokerOrder:
        return self.orders[alpaca_order_id]

    def get_account(self) -> dict:
        return self.account

    def get_positions(self) -> list[dict]:
        return self.positions

    def get_position(self, symbol: str) -> dict | None:
        for position in self.positions:
            if position["symbol"] == symbol:
                return position
        return None
