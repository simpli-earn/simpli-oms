from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Fractional equity fills need more than money's usual 4 places. 10 covers
# Alpaca notionals and the 9-place share quantities already stored locally.
AMOUNT = Numeric(24, 10)


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cash_budget: Mapped[Decimal] = mapped_column(AMOUNT)
    cash_remaining: Mapped[Decimal] = mapped_column(AMOUNT)
    cash_reserved: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    positions: Mapped[list["StrategyPosition"]] = relationship(back_populates="strategy")
    orders: Mapped[list["Order"]] = relationship(back_populates="strategy")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("strategy_id", "client_order_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    order_class: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    type: Mapped[str] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(AMOUNT)
    limit_price: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(8))
    position_intent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_new", index=True)
    filled_qty: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    filled_avg_price: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    applied_notional: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    reserved_notional: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    strategy: Mapped[Strategy] = relationship(back_populates="orders")
    legs: Mapped[list["OrderLeg"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    fills: Mapped[list["Fill"]] = relationship(back_populates="order")


class OrderLeg(Base):
    __tablename__ = "order_legs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    ratio_qty: Mapped[Decimal] = mapped_column(AMOUNT)
    side: Mapped[str] = mapped_column(String(8))
    position_intent: Mapped[str] = mapped_column(String(16))
    filled_qty: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    filled_avg_price: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    applied_notional: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))

    order: Mapped[Order] = relationship(back_populates="legs")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[Decimal] = mapped_column(AMOUNT)
    price: Mapped[Decimal] = mapped_column(AMOUNT)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="fills")


class StrategyPosition(Base):
    __tablename__ = "strategy_positions"
    __table_args__ = (UniqueConstraint("strategy_id", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    avg_price: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))

    strategy: Mapped[Strategy] = relationship(back_populates="positions")


class LedgerEntry(Base):
    """Append-only cash and position deltas.

    Sum of cash_delta for a strategy equals its spendable cash_remaining.
    Spendable cash plus cash_reserved is the settled cash. Sum of qty_delta
    for a strategy and symbol equals that position's quantity.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    cash_delta: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    qty_delta: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    price: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StrategyCode(Base):
    """The cloud copy of a strategy's folder. revision goes up by one on every upload, so a
    laptop that uploads from an older revision is told someone else changed it first."""

    __tablename__ = "strategy_code"

    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    schedule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StrategyFile(Base):
    """The latest copy of one file of a strategy's folder (code, strategy.json, versions/, models/)."""

    __tablename__ = "strategy_files"

    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), primary_key=True)
    path: Mapped[str] = mapped_column(String(512), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Prediction-market paper trading. Separate cash from the Alpaca books above; fills are
# simulated against the live Kalshi or Polymarket order book. Prices are dollars per contract.


class PmAccount(Base):
    __tablename__ = "pm_accounts"

    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), primary_key=True)
    cash_budget: Mapped[Decimal] = mapped_column(AMOUNT)
    cash: Mapped[Decimal] = mapped_column(AMOUNT)
    cash_reserved: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    fees_paid: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PmOrder(Base):
    __tablename__ = "pm_orders"
    __table_args__ = (UniqueConstraint("strategy_id", "client_order_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("pm_accounts.strategy_id"), index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    venue: Mapped[str] = mapped_column(String(16))
    market_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    outcome: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(8))
    count: Mapped[Decimal] = mapped_column(AMOUNT)
    limit_price: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    filled_count: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    avg_price: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    fees: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    reserved: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PmFill(Base):
    __tablename__ = "pm_fills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("pm_orders.id"), nullable=True, index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("pm_accounts.strategy_id"), index=True)
    venue: Mapped[str] = mapped_column(String(16))
    market_id: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(8))  # buy, sell, or settle
    count: Mapped[Decimal] = mapped_column(AMOUNT)
    price: Mapped[Decimal] = mapped_column(AMOUNT)
    fee: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PmPosition(Base):
    __tablename__ = "pm_positions"
    __table_args__ = (UniqueConstraint("strategy_id", "venue", "market_id", "outcome"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("pm_accounts.strategy_id"), index=True)
    venue: Mapped[str] = mapped_column(String(16))
    market_id: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(500), default="")
    count: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    avg_price: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
