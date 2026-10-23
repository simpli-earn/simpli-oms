import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.errors import AppError
from app.models import Order, OrderLeg, Strategy, utcnow
from app.schemas import OrderIn
from app.services.broker import Broker, BrokerError, SubmitLeg, SubmitRequest
from app.services.ledger import TERMINAL_STATUSES, ensure_affordable, ensure_closable


def place_order(db: Session, broker: Broker, data: OrderIn) -> tuple[Order, bool]:
    strategy = db.get(Strategy, data.strategy_id)
    if strategy is None:
        raise AppError(404, "unknown strategy")

    if data.client_order_id:
        existing = _by_client_id(db, data.strategy_id, data.client_order_id)
        if existing is not None:
            return existing, False

    ensure_affordable(strategy, data)
    ensure_closable(db, strategy, data)

    order = Order(
        id=str(uuid.uuid4()),
        strategy_id=data.strategy_id,
        client_order_id=data.client_order_id,
        asset_class=data.asset_class or "us_equity",
        order_class=data.order_class,
        symbol=data.symbol,
        side=data.side,
        type=data.type,
        qty=data.qty or 0,
        limit_price=data.limit_price,
        stop_price=data.stop_price,
        time_in_force=data.time_in_force,
        position_intent=data.position_intent,
        status="pending_new",
        request_payload=data.model_dump(mode="json"),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    if data.legs:
        order.legs = [
            OrderLeg(
                symbol=leg.symbol,
                ratio_qty=leg.ratio_qty,
                side=leg.side,
                position_intent=leg.position_intent,
            )
            for leg in data.legs
        ]
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if data.client_order_id:
            existing = _by_client_id(db, data.strategy_id, data.client_order_id)
            if existing is not None:
                return existing, False
        raise

    request = SubmitRequest(
        client_order_id=order.id,
        order_class=order.order_class,
        asset_class=order.asset_class,
        symbol=order.symbol,
        side=order.side,
        type=order.type,
        qty=order.qty,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        time_in_force=order.time_in_force,
        position_intent=order.position_intent,
        notional=data.notional,
        legs=[
            SubmitLeg(
                symbol=leg.symbol,
                ratio_qty=leg.ratio_qty,
                side=leg.side,
                position_intent=leg.position_intent,
            )
            for leg in order.legs
        ],
    )
    try:
        remote = broker.submit(request)
    except BrokerError as exc:
        order.status = "rejected"
        order.reject_reason = str(exc)[:500]
        order.updated_at = utcnow()
        db.commit()
        raise AppError(502, str(exc)) from exc

    order.alpaca_order_id = remote.id
    order.status = (remote.status or "accepted").lower()
    order.updated_at = utcnow()
    db.commit()
    return order, True


def cancel_order(db: Session, broker: Broker, order_id: str) -> Order:
    order = db.scalar(select(Order).where(Order.id == order_id).options(selectinload(Order.legs)))
    if order is None:
        raise AppError(404, "order not found")
    if order.status in TERMINAL_STATUSES:
        raise AppError(400, f"order is {order.status}")
    if not order.alpaca_order_id:
        order.status = "canceled"
        order.updated_at = utcnow()
        db.commit()
        return order
    try:
        broker.cancel(order.alpaca_order_id)
    except BrokerError as exc:
        raise AppError(502, str(exc)) from exc
    order.status = "pending_cancel"
    order.updated_at = utcnow()
    db.commit()
    return order


def get_order(db: Session, order_id: str) -> Order:
    order = db.scalar(select(Order).where(Order.id == order_id).options(selectinload(Order.legs)))
    if order is None:
        raise AppError(404, "order not found")
    return order


def list_orders(db: Session, strategy_id: str | None) -> list[Order]:
    stmt = select(Order).options(selectinload(Order.legs)).order_by(Order.created_at.desc()).limit(200)
    if strategy_id:
        stmt = stmt.where(Order.strategy_id == strategy_id)
    return list(db.scalars(stmt))


def _by_client_id(db: Session, strategy_id: str, client_order_id: str) -> Order | None:
    return db.scalar(
        select(Order)
        .where(Order.strategy_id == strategy_id, Order.client_order_id == client_order_id)
        .options(selectinload(Order.legs))
    )
