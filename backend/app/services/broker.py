from dataclasses import dataclass, field
from typing import Protocol

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from app.config import Settings

INTENTS = {
    "buy_to_open": PositionIntent.BUY_TO_OPEN,
    "buy_to_close": PositionIntent.BUY_TO_CLOSE,
    "sell_to_open": PositionIntent.SELL_TO_OPEN,
    "sell_to_close": PositionIntent.SELL_TO_CLOSE,
}


class BrokerError(Exception):
    pass


@dataclass
class SubmitLeg:
    symbol: str
    ratio_qty: float
    side: str
    position_intent: str


@dataclass
class SubmitRequest:
    client_order_id: str
    order_class: str
    asset_class: str
    symbol: str | None
    side: str | None
    type: str
    qty: float
    limit_price: float | None
    stop_price: float | None
    time_in_force: str
    position_intent: str | None
    legs: list[SubmitLeg] = field(default_factory=list)


@dataclass
class BrokerLeg:
    symbol: str
    side: str
    position_intent: str | None
    ratio_qty: float
    filled_qty: float = 0
    filled_avg_price: float | None = None


@dataclass
class BrokerOrder:
    id: str
    status: str
    filled_qty: float = 0
    filled_avg_price: float | None = None
    legs: list[BrokerLeg] = field(default_factory=list)


class Broker(Protocol):
    def submit(self, request: SubmitRequest) -> BrokerOrder: ...

    def cancel(self, alpaca_order_id: str) -> None: ...

    def get_order(self, alpaca_order_id: str) -> BrokerOrder: ...

    def get_account(self) -> dict: ...

    def get_positions(self) -> list[dict]: ...

    def get_position(self, symbol: str) -> dict | None: ...


def build_alpaca_request(request: SubmitRequest):
    tif = TimeInForce.DAY if request.time_in_force == "day" else TimeInForce.GTC
    if request.order_class == "mleg":
        legs = [
            OptionLegRequest(
                symbol=leg.symbol,
                ratio_qty=leg.ratio_qty,
                side=_side(leg.side),
                position_intent=INTENTS[leg.position_intent],
            )
            for leg in request.legs
        ]
        if request.type == "market":
            return MarketOrderRequest(
                qty=request.qty,
                time_in_force=tif,
                order_class=OrderClass.MLEG,
                legs=legs,
                client_order_id=request.client_order_id,
            )
        return LimitOrderRequest(
            qty=request.qty,
            time_in_force=tif,
            order_class=OrderClass.MLEG,
            legs=legs,
            limit_price=request.limit_price,
            client_order_id=request.client_order_id,
        )

    common = {
        "symbol": request.symbol,
        "qty": request.qty,
        "side": _side(request.side or "buy"),
        "time_in_force": tif,
        "client_order_id": request.client_order_id,
    }
    if request.position_intent:
        common["position_intent"] = INTENTS[request.position_intent]
    if request.type == "market":
        return MarketOrderRequest(**common)
    if request.type == "limit":
        return LimitOrderRequest(limit_price=request.limit_price, **common)
    if request.type == "stop":
        return StopOrderRequest(stop_price=request.stop_price, **common)
    return StopLimitOrderRequest(
        stop_price=request.stop_price,
        limit_price=request.limit_price,
        **common,
    )


class AlpacaBroker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: TradingClient | None = None

    def submit(self, request: SubmitRequest) -> BrokerOrder:
        try:
            order = self._trading().submit_order(build_alpaca_request(request))
        except Exception as exc:
            raise BrokerError(_message(exc)) from exc
        return _broker_order(order)

    def cancel(self, alpaca_order_id: str) -> None:
        try:
            self._trading().cancel_order_by_id(alpaca_order_id)
        except Exception as exc:
            raise BrokerError(_message(exc)) from exc

    def get_order(self, alpaca_order_id: str) -> BrokerOrder:
        try:
            order = self._trading().get_order_by_id(alpaca_order_id)
        except Exception as exc:
            raise BrokerError(_message(exc)) from exc
        return _broker_order(order)

    def get_account(self) -> dict:
        try:
            account = self._trading().get_account()
        except Exception as exc:
            raise BrokerError(_message(exc)) from exc
        equity = _num(account.equity)
        return {
            "id": str(account.id),
            "status": _text(account.status),
            "currency": str(getattr(account, "currency", "USD") or "USD"),
            "equity": equity,
            "cash": _num(account.cash),
            "buying_power": _num(account.buying_power),
            "portfolio_value": _num(getattr(account, "portfolio_value", None)) or equity,
        }

    def get_positions(self) -> list[dict]:
        try:
            positions = self._trading().get_all_positions()
        except Exception as exc:
            raise BrokerError(_message(exc)) from exc
        return [_position(item) for item in positions]

    def get_position(self, symbol: str) -> dict | None:
        try:
            position = self._trading().get_open_position(symbol)
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise BrokerError(_message(exc)) from exc
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise BrokerError(_message(exc)) from exc
        return _position(position)

    def _trading(self) -> TradingClient:
        if not self.settings.apca_api_key_id or not self.settings.apca_api_secret_key:
            raise BrokerError("Alpaca paper API key and secret are not configured")
        if self._client is None:
            self._client = TradingClient(
                api_key=self.settings.apca_api_key_id,
                secret_key=self.settings.apca_api_secret_key,
                paper=True,
                url_override=self.settings.apca_api_base_url or None,
            )
        return self._client


def _side(value: str) -> OrderSide:
    return OrderSide.BUY if value == "buy" else OrderSide.SELL


def _broker_order(order) -> BrokerOrder:
    legs = []
    for leg in getattr(order, "legs", None) or []:
        intent = _text(getattr(leg, "position_intent", None)).lower()
        legs.append(
            BrokerLeg(
                symbol=str(leg.symbol),
                side=_text(leg.side).lower(),
                position_intent=intent or None,
                ratio_qty=_num(getattr(leg, "ratio_qty", 1)) or 1,
                filled_qty=_num(leg.filled_qty),
                filled_avg_price=_num_or_none(leg.filled_avg_price),
            )
        )
    return BrokerOrder(
        id=str(order.id),
        status=_text(order.status).lower(),
        filled_qty=_num(order.filled_qty),
        filled_avg_price=_num_or_none(order.filled_avg_price),
        legs=legs,
    )


def _position(position) -> dict:
    asset_class = _text(getattr(position, "asset_class", "")).lower()
    return {
        "symbol": str(position.symbol),
        "qty": _num(position.qty),
        "avg_entry_price": _num(position.avg_entry_price),
        "market_value": _num(position.market_value),
        "unrealized_pl": _num(position.unrealized_pl),
        "current_price": _num(position.current_price),
        "side": _text(position.side).lower(),
        "asset_class": asset_class,
    }


def _message(exc: Exception) -> str:
    if isinstance(exc, BrokerError):
        return str(exc)
    try:
        message = exc.message  # type: ignore[attr-defined]
        if isinstance(message, str) and message:
            return message
    except Exception:
        pass
    return str(exc)


def _text(value) -> str:
    if value is None:
        return ""
    raw = value.value if hasattr(value, "value") else value
    return str(raw)


def _num(value) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _num_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
