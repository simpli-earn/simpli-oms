from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.errors import AppError
from app.models import Fill, LedgerEntry, Order, OrderLeg, Strategy, StrategyPosition, utcnow
from app.schemas import OrderIn
from app.services.broker import BrokerLeg, BrokerOrder

TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced"}
OPTION_MULTIPLIER = Decimal("100")
CASH = Decimal("0.0001")
QTY = Decimal("0.000000001")
ZERO = Decimal("0")


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO
    return Decimal(str(value))


def qcash(value) -> Decimal:
    return D(value).quantize(CASH)


def qqty(value) -> Decimal:
    return D(value).quantize(QTY)


def locked(db: Session, stmt: Select, *entities) -> Select:
    """Lock the primary rows on Postgres. SQLite tests ignore row locks."""
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql" and entities:
        return stmt.with_for_update(of=entities)
    return stmt


def debit_cost(order: OrderIn) -> Decimal | None:
    if order.notional is not None:
        return qcash(order.notional)
    if order.order_class == "mleg":
        if order.type != "limit" or order.limit_price is None or order.limit_price <= 0:
            return None
        return qcash(D(order.limit_price) * D(order.qty or 0) * OPTION_MULTIPLIER)
    if order.side != "buy" or order.limit_price is None:
        return None
    multiplier = OPTION_MULTIPLIER if order.asset_class == "us_option" else Decimal("1")
    return qcash(D(order.limit_price) * D(order.qty or 0) * multiplier)


def reserve_cash(db: Session, strategy: Strategy, order: Order, cost: Decimal) -> None:
    """Move spendable cash into the reservation. The WHERE clause is the lock."""
    result = db.execute(
        update(Strategy)
        .where(Strategy.id == strategy.id, Strategy.cash_remaining >= cost)
        .values(
            cash_remaining=Strategy.cash_remaining - cost,
            cash_reserved=Strategy.cash_reserved + cost,
        )
    )
    if result.rowcount != 1:
        db.expire(strategy)
        available = qcash(strategy.cash_remaining)
        raise AppError(
            400,
            f"strategy cash {available:.2f} is below order cost {cost:.2f}",
        )
    order.reserved_notional = cost
    db.expire(strategy)
    record_ledger(db, strategy.id, order.id, None, "reserve", -cost, ZERO, None)


def release_reservation(db: Session, strategy: Strategy, order: Order) -> None:
    left = qcash(order.reserved_notional)
    if left == 0:
        return
    db.expire(strategy)
    strategy.cash_remaining = qcash(strategy.cash_remaining) + left
    strategy.cash_reserved = qcash(strategy.cash_reserved) - left
    order.reserved_notional = ZERO
    record_ledger(db, strategy.id, order.id, None, "release", left, ZERO, None)


def ensure_closable(db: Session, strategy: Strategy, order: OrderIn) -> None:
    if order.order_class == "mleg":
        for leg in order.legs or []:
            _ensure_leg(
                _held(db, strategy.id, leg.symbol),
                leg.symbol,
                leg.position_intent,
                leg.side,
                D(order.qty or 0) * D(leg.ratio_qty),
            )
        return
    _ensure_leg(
        _held(db, strategy.id, order.symbol or ""),
        order.symbol or "",
        order.position_intent,
        order.side,
        D(order.qty or 0),
    )


def apply_broker_update(db: Session, strategy: Strategy, order: Order, remote: BrokerOrder) -> None:
    if remote.status:
        order.status = remote.status.lower()
    if order.order_class == "mleg":
        _apply_mleg(db, strategy, order, remote)
    else:
        _apply_simple(db, strategy, order, remote)
    if order.status in TERMINAL_STATUSES:
        release_reservation(db, strategy, order)
    order.updated_at = utcnow()


def record_ledger(
    db: Session,
    strategy_id: str,
    order_id: str | None,
    symbol: str | None,
    kind: str,
    cash_delta,
    qty_delta,
    price,
) -> None:
    cash = qcash(cash_delta)
    qty = qqty(qty_delta)
    if cash == 0 and qty == 0:
        return
    db.add(
        LedgerEntry(
            strategy_id=strategy_id,
            order_id=order_id,
            symbol=symbol,
            kind=kind,
            cash_delta=cash,
            qty_delta=qty,
            price=None if price is None else qqty(price),
            created_at=utcnow(),
        )
    )


def unrealized_pnl(positions: list[StrategyPosition], prices: dict[str, float] | None) -> float | None:
    if prices is None:
        return None
    total = ZERO
    for pos in positions:
        if abs(D(pos.qty)) < QTY:
            continue
        price = prices.get(pos.symbol)
        if price is None:
            continue
        multiplier = OPTION_MULTIPLIER if pos.asset_class == "us_option" else Decimal("1")
        total += (D(price) - D(pos.avg_price)) * D(pos.qty) * multiplier
    return float(qcash(total))


def _held(db: Session, strategy_id: str, symbol: str) -> Decimal:
    pos = db.scalar(
        select(StrategyPosition).where(
            StrategyPosition.strategy_id == strategy_id,
            StrategyPosition.symbol == symbol,
        )
    )
    return D(pos.qty) if pos else ZERO


def _ensure_leg(held: Decimal, symbol: str, intent: str | None, side: str | None, qty: Decimal) -> None:
    closing_long = intent == "sell_to_close" or (intent is None and side == "sell")
    closing_short = intent == "buy_to_close"
    if closing_long and held + QTY < qty:
        raise AppError(400, f"not enough {symbol} position to sell {qty:g} (held {held:g})")
    if closing_short and held - QTY > -qty:
        raise AppError(400, f"not enough short {symbol} to cover {qty:g} (held {held:g})")


def _apply_simple(db: Session, strategy: Strategy, order: Order, remote: BrokerOrder) -> None:
    if order.symbol is None or order.side is None:
        return
    multiplier = OPTION_MULTIPLIER if order.asset_class == "us_option" else Decimal("1")
    new_qty = qqty(remote.filled_qty or 0)
    new_avg = D(remote.filled_avg_price or 0)
    new_notional = qcash(new_avg * new_qty * multiplier)
    delta_qty = qqty(new_qty - D(order.filled_qty))
    delta_notional = qcash(new_notional - D(order.applied_notional))
    order.filled_qty = new_qty
    order.filled_avg_price = None if remote.filled_avg_price is None else qqty(new_avg)
    order.applied_notional = new_notional
    if abs(delta_qty) < QTY and abs(delta_notional) < CASH:
        return
    price = (delta_notional / (delta_qty * multiplier)) if abs(delta_qty) >= QTY else new_avg
    cash_delta = _apply_fill_cash(strategy, order, delta_qty, delta_notional)
    signed = ZERO
    if abs(delta_qty) >= QTY:
        signed = delta_qty if order.side == "buy" else -delta_qty
        _apply_position(db, strategy, order.symbol, order.asset_class, signed, price)
        db.add(
            Fill(
                order_id=order.id,
                strategy_id=order.strategy_id,
                symbol=order.symbol,
                side=order.side,
                qty=abs(delta_qty),
                price=qqty(price),
                filled_at=datetime.now(timezone.utc),
            )
        )
    record_ledger(db, strategy.id, order.id, order.symbol, "fill", cash_delta, signed, price)


def _apply_mleg(db: Session, strategy: Strategy, order: Order, remote: BrokerOrder) -> None:
    new_qty = qqty(remote.filled_qty or 0)
    new_avg = D(remote.filled_avg_price or 0)
    new_notional = qcash(new_avg * new_qty * OPTION_MULTIPLIER)
    delta_qty = qqty(new_qty - D(order.filled_qty))
    delta_notional = qcash(new_notional - D(order.applied_notional))
    order.filled_qty = new_qty
    order.filled_avg_price = None if remote.filled_avg_price is None else qqty(new_avg)
    order.applied_notional = new_notional
    if abs(delta_notional) >= CASH or abs(delta_qty) >= QTY:
        cash_delta = _apply_fill_cash(strategy, order, delta_qty, delta_notional)
        record_ledger(db, strategy.id, order.id, None, "fill", cash_delta, ZERO, new_avg)

    for remote_leg in remote.legs or []:
        leg = _match_leg(order, remote_leg)
        if leg is None:
            continue
        leg_qty = qqty(remote_leg.filled_qty or 0)
        leg_avg = D(remote_leg.filled_avg_price or 0)
        leg_notional = qcash(leg_avg * leg_qty * OPTION_MULTIPLIER)
        delta_leg = qqty(leg_qty - D(leg.filled_qty))
        previous_notional = D(leg.applied_notional)
        leg.filled_qty = leg_qty
        leg.filled_avg_price = None if remote_leg.filled_avg_price is None else qqty(leg_avg)
        leg.applied_notional = leg_notional
        if abs(delta_leg) < QTY:
            continue
        price = (leg_notional - previous_notional) / (delta_leg * OPTION_MULTIPLIER)
        signed = delta_leg if leg.side == "buy" else -delta_leg
        _apply_position(db, strategy, leg.symbol, "us_option", signed, price)
        db.add(
            Fill(
                order_id=order.id,
                strategy_id=order.strategy_id,
                symbol=leg.symbol,
                side=leg.side,
                qty=abs(delta_leg),
                price=qqty(price),
                filled_at=datetime.now(timezone.utc),
            )
        )
        record_ledger(db, strategy.id, order.id, leg.symbol, "fill", ZERO, signed, price)


def _apply_fill_cash(strategy: Strategy, order: Order, delta_qty: Decimal, delta_notional: Decimal) -> Decimal:
    actual = qcash(delta_notional)
    consumed = _reserved_slice(order, delta_qty, actual)
    if consumed > 0:
        order.reserved_notional = qcash(order.reserved_notional) - consumed
        strategy.cash_reserved = qcash(strategy.cash_reserved) - consumed
    if consumed > 0 and (order.side == "buy" or order.order_class == "mleg"):
        cash_delta = qcash(consumed - actual)
    elif order.order_class == "mleg":
        cash_delta = qcash(-actual)
    elif order.side == "buy":
        cash_delta = qcash(-actual)
    else:
        cash_delta = actual
    if cash_delta != 0:
        strategy.cash_remaining = qcash(strategy.cash_remaining) + cash_delta
    return cash_delta


def _reserved_slice(order: Order, delta_qty: Decimal, actual: Decimal) -> Decimal:
    reserved = qcash(order.reserved_notional)
    if reserved <= 0:
        return ZERO
    if order.order_class == "mleg" and order.limit_price is not None and D(order.limit_price) > 0:
        return min(reserved, qcash(abs(delta_qty) * D(order.limit_price) * OPTION_MULTIPLIER))
    if order.limit_price is not None and order.side == "buy":
        multiplier = OPTION_MULTIPLIER if order.asset_class == "us_option" else Decimal("1")
        return min(reserved, qcash(abs(delta_qty) * D(order.limit_price) * multiplier))
    return min(reserved, qcash(max(actual, ZERO)))


def _match_leg(order: Order, remote: BrokerLeg) -> OrderLeg | None:
    for leg in order.legs:
        if leg.symbol == remote.symbol and leg.side == remote.side:
            return leg
    return None


def _apply_position(
    db: Session,
    strategy: Strategy,
    symbol: str,
    asset_class: str,
    signed_qty: Decimal,
    price: Decimal,
) -> None:
    pos = db.scalar(
        select(StrategyPosition).where(
            StrategyPosition.strategy_id == strategy.id,
            StrategyPosition.symbol == symbol,
        )
    )
    if pos is None:
        pos = StrategyPosition(
            strategy_id=strategy.id,
            symbol=symbol,
            asset_class=asset_class,
            qty=ZERO,
            avg_price=ZERO,
        )
        db.add(pos)
    multiplier = OPTION_MULTIPLIER if asset_class == "us_option" else Decimal("1")
    old = D(pos.qty)
    new = old + signed_qty
    if abs(new) < QTY:
        new = ZERO
    price = D(price)
    if old == 0 or old * signed_qty > 0:
        gross = abs(old) * D(pos.avg_price) + abs(signed_qty) * price
        pos.avg_price = qqty(gross / abs(new)) if new else ZERO
    elif new == 0 or old * new > 0:
        closed = abs(signed_qty)
        strategy.realized_pnl = qcash(strategy.realized_pnl) + _realized(old, pos.avg_price, price, closed, multiplier)
    else:
        closed = abs(old)
        strategy.realized_pnl = qcash(strategy.realized_pnl) + _realized(old, pos.avg_price, price, closed, multiplier)
        pos.avg_price = qqty(price)
    pos.qty = qqty(new)


def _realized(old_qty: Decimal, avg, price: Decimal, closed: Decimal, multiplier: Decimal) -> Decimal:
    if old_qty > 0:
        return qcash((price - D(avg)) * closed * multiplier)
    return qcash((D(avg) - price) * closed * multiplier)
