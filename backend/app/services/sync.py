import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Order, Strategy
from app.services.broker import Broker, BrokerError
from app.services.ledger import TERMINAL_STATUSES, apply_broker_update, locked

logger = logging.getLogger(__name__)


def sync_once(session_factory, broker: Broker) -> int:
    """Apply each open order under its own row locks.

    The broker read stays outside the transaction. Two machines can fetch the
    same fill; the order lock makes the second application a zero delta.
    """
    db: Session = session_factory()
    try:
        rows = db.execute(
            select(Order.id, Order.alpaca_order_id, Order.strategy_id).where(
                Order.alpaca_order_id.is_not(None),
                Order.status.notin_(TERMINAL_STATUSES),
            )
        ).all()
    finally:
        db.close()

    updated = 0
    for order_id, alpaca_order_id, strategy_id in rows:
        try:
            remote = broker.get_order(alpaca_order_id)
        except BrokerError:
            logger.exception("order sync failed for %s", order_id)
            continue
        db = session_factory()
        try:
            strategy = db.scalar(locked(db, select(Strategy).where(Strategy.id == strategy_id), Strategy))
            order = db.scalar(
                locked(
                    db,
                    select(Order).where(Order.id == order_id).options(selectinload(Order.legs)),
                    Order,
                )
            )
            if strategy is None or order is None or order.status in TERMINAL_STATUSES:
                db.rollback()
                continue
            apply_broker_update(db, strategy, order, remote)
            db.commit()
            updated += 1
        except Exception:
            db.rollback()
            logger.exception("order sync failed for %s", order_id)
        finally:
            db.close()
    return updated
