from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cash_budget: Mapped[float] = mapped_column(Float)
    cash_remaining: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
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
    qty: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(8))
    position_intent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_new", index=True)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    applied_notional: Mapped[float] = mapped_column(Float, default=0.0)
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
    ratio_qty: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(8))
    position_intent: Mapped[str] = mapped_column(String(16))
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    applied_notional: Mapped[float] = mapped_column(Float, default=0.0)

    order: Mapped[Order] = relationship(back_populates="legs")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="fills")


class StrategyPosition(Base):
    __tablename__ = "strategy_positions"
    __table_args__ = (UniqueConstraint("strategy_id", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)

    strategy: Mapped[Strategy] = relationship(back_populates="positions")
