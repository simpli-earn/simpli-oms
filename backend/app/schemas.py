import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

STRATEGY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
INTENTS = ("buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close")
Intent = Literal["buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"]
Side = Literal["buy", "sell"]
OrderTypeName = Literal["market", "limit", "stop", "stop_limit"]


def _clean_strategy_id(value: str) -> str:
    cleaned = value.strip()
    if not STRATEGY_ID.fullmatch(cleaned):
        raise ValueError("strategy_id must be 1-64 letters, numbers, hyphens, or underscores")
    return cleaned


class StrategyIn(BaseModel):
    id: str
    cash_budget: float = Field(gt=0)

    @model_validator(mode="after")
    def normalize(self):
        self.id = _clean_strategy_id(self.id)
        return self


class StrategyOut(BaseModel):
    id: str
    cash_budget: float
    cash_remaining: float
    realized_pnl: float
    unrealized_pnl: float | None = None
    created_at: datetime


class LegIn(BaseModel):
    symbol: str
    ratio_qty: float = Field(default=1, gt=0)
    side: Side
    position_intent: Intent

    @model_validator(mode="after")
    def normalize(self):
        expected = "buy" if self.position_intent.startswith("buy") else "sell"
        if self.side != expected:
            raise ValueError(f"leg side {self.side} does not match {self.position_intent}")
        self.symbol = self.symbol.strip().upper()
        if not self.symbol:
            raise ValueError("leg symbol is required")
        return self


class OrderIn(BaseModel):
    strategy_id: str
    client_order_id: str | None = None
    order_class: Literal["simple", "mleg"] = "simple"
    asset_class: Literal["us_equity", "us_option"] | None = None
    symbol: str | None = None
    side: Side | None = None
    type: OrderTypeName
    qty: float = Field(gt=0)
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: Literal["day", "gtc"] = "day"
    position_intent: Intent | None = None
    legs: list[LegIn] | None = None

    @model_validator(mode="after")
    def normalize(self):
        self.strategy_id = _clean_strategy_id(self.strategy_id)
        if self.client_order_id is not None:
            self.client_order_id = self.client_order_id.strip()
            if not self.client_order_id:
                self.client_order_id = None
            elif len(self.client_order_id) > 48:
                raise ValueError("client_order_id must be 48 characters or fewer")

        if self.order_class == "mleg":
            if self.type not in ("market", "limit"):
                raise ValueError("multi-leg orders must be market or limit")
            if not self.legs or not 2 <= len(self.legs) <= 4:
                raise ValueError("multi-leg orders need 2 to 4 legs")
            self.asset_class = "us_option"
            self.symbol = None
            self.side = None
            self.position_intent = None
            self.stop_price = None
        else:
            if not self.symbol or not self.symbol.strip():
                raise ValueError("symbol is required")
            self.symbol = self.symbol.strip().upper()
            if self.position_intent:
                expected = "buy" if self.position_intent.startswith("buy") else "sell"
                if self.side and self.side != expected:
                    raise ValueError("side does not match position_intent")
                self.side = expected
                if self.asset_class == "us_equity":
                    raise ValueError("position_intent is only valid for options")
                self.asset_class = "us_option"
            else:
                if not self.side:
                    raise ValueError("side is required")
                if self.asset_class is None:
                    self.asset_class = "us_equity"
                if self.asset_class == "us_option":
                    raise ValueError("options orders require position_intent")
            self.legs = None

        whole = self.asset_class == "us_option"
        if whole and abs(self.qty - round(self.qty)) > 1e-9:
            raise ValueError("option quantity must be a whole number of contracts")

        if self.type == "market":
            if self.limit_price is not None or self.stop_price is not None:
                raise ValueError("market orders cannot include limit_price or stop_price")
        elif self.type == "limit":
            if self.limit_price is None:
                raise ValueError("limit orders require limit_price")
            if self.stop_price is not None:
                raise ValueError("limit orders cannot include stop_price")
        elif self.type == "stop":
            if self.stop_price is None:
                raise ValueError("stop orders require stop_price")
            if self.limit_price is not None:
                raise ValueError("stop orders cannot include limit_price")
        elif self.limit_price is None or self.stop_price is None:
            raise ValueError("stop_limit orders require limit_price and stop_price")

        if self.order_class == "mleg" and self.type == "limit" and self.limit_price == 0:
            raise ValueError("multi-leg limit_price must be a positive debit or a negative credit")
        if self.order_class == "simple" and self.type in ("limit", "stop_limit"):
            if self.limit_price is not None and self.limit_price <= 0:
                raise ValueError("limit_price must be positive")
        if self.stop_price is not None and self.stop_price <= 0:
            raise ValueError("stop_price must be positive")
        return self


class LegOut(BaseModel):
    symbol: str
    ratio_qty: float
    side: str
    position_intent: str
    filled_qty: float
    filled_avg_price: float | None = None


class OrderOut(BaseModel):
    id: str
    strategy_id: str
    client_order_id: str | None
    alpaca_order_id: str | None
    asset_class: str
    order_class: str
    symbol: str | None
    side: str | None
    type: str
    qty: float
    limit_price: float | None
    stop_price: float | None
    time_in_force: str
    position_intent: str | None
    status: str
    filled_qty: float
    filled_avg_price: float | None
    reject_reason: str | None
    legs: list[LegOut]
    created_at: datetime
    updated_at: datetime


class FillOut(BaseModel):
    id: int
    order_id: str
    strategy_id: str
    symbol: str
    side: str
    qty: float
    price: float
    filled_at: datetime


class PositionOut(BaseModel):
    strategy_id: str
    symbol: str
    asset_class: str
    qty: float
    avg_price: float
    unrealized_pnl: float | None = None


class AccountOut(BaseModel):
    id: str
    status: str
    currency: str
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float


class AccountPositionOut(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    current_price: float
    side: str
    asset_class: str


class BookOut(BaseModel):
    strategy: StrategyOut
    positions: list[PositionOut]
    orders: list[OrderOut]
    fills: list[FillOut]


class StrategyTrades(BaseModel):
    strategy_id: str
    qty: float
    avg_price: float
    asset_class: str
    trades: list[FillOut]


class SymbolDetailOut(BaseModel):
    symbol: str
    account_position: AccountPositionOut | None
    account_error: str | None = None
    strategies: list[StrategyTrades]
