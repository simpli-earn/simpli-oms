import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Order
from app.services.broker import Broker, BrokerError
from app.services.ledger import TERMINAL_STATUSES, apply_broker_update

logger = logging.getLogger(__name__)


def sync_once(session_factory, broker: Broker) -> int:
    db: Session = session_factory()
    updated = 0
    try:
        rows = db.execute(
            select(Order.id, Order.alpaca_order_id).where(
                Order.alpaca_order_id.is_not(None),
                Order.status.notin_(TERMINAL_STATUSES),
            )
        ).all()
        for order_id, alpaca_order_id in rows:
            try:
                remote = broker.get_order(alpaca_order_id)
                order = db.scalar(
                    select(Order).where(Order.id == order_id).options(selectinload(Order.legs))
                )
                if order is None:
                    continue
                apply_broker_update(db, order, remote)
                db.commit()
                updated += 1
            except BrokerError:
                db.rollback()
                logger.exception("order sync failed for %s", order_id)
            except Exception:
                db.rollback()
                logger.exception("order sync failed for %s", order_id)
        return updated
    finally:
        db.close()
