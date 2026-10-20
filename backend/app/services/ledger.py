from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Fill, Order, OrderLeg, Strategy, StrategyPosition, utcnow
from app.schemas import OrderIn
from app.services.broker import BrokerLeg, BrokerOrder

TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced"}
OPTION_MULTIPLIER = 100.0


def debit_cost(order: OrderIn) -> float | None:
    if order.order_class == "mleg":
        if order.type != "limit" or order.limit_price is None or order.limit_price <= 0:
            return None
        return round(order.limit_price * order.qty * OPTION_MULTIPLIER, 4)
    if order.side != "buy" or order.limit_price is None:
        return None
    multiplier = OPTION_MULTIPLIER if order.asset_class == "us_option" else 1.0
    return round(order.limit_price * order.qty * multiplier, 4)


def ensure_affordable(strategy: Strategy, order: OrderIn) -> None:
    cost = debit_cost(order)
    if cost is None:
        return
    if cost > strategy.cash_remaining + 1e-6:
        raise AppError(
            400,
            f"strategy cash {strategy.cash_remaining:.2f} is below order cost {cost:.2f}",
        )


def ensure_closable(db: Session, strategy: Strategy, order: OrderIn) -> None:
    if order.order_class == "mleg":
        for leg in order.legs or []:
            _ensure_leg(
                _held(db, strategy.id, leg.symbol),
                leg.symbol,
                leg.position_intent,
                leg.side,
                order.qty * leg.ratio_qty,
            )
        return
    _ensure_leg(
        _held(db, strategy.id, order.symbol or ""),
        order.symbol or "",
        order.position_intent,
        order.side,
        order.qty,
    )


def apply_broker_update(db: Session, order: Order, remote: BrokerOrder) -> None:
    if remote.status:
        order.status = remote.status.lower()
    if order.order_class == "mleg":
        _apply_mleg(db, order, remote)
    else:
        _apply_simple(db, order, remote)
    order.updated_at = utcnow()


def unrealized_pnl(positions: list[StrategyPosition], prices: dict[str, float] | None) -> float | None:
    if prices is None:
        return None
    total = 0.0
    for pos in positions:
        if abs(pos.qty) < 1e-8:
            continue
        price = prices.get(pos.symbol)
        if price is None:
            continue
        multiplier = OPTION_MULTIPLIER if pos.asset_class == "us_option" else 1.0
        total += (price - pos.avg_price) * pos.qty * multiplier
    return round(total, 4)


def _held(db: Session, strategy_id: str, symbol: str) -> float:
    pos = db.scalar(
        select(StrategyPosition).where(
            StrategyPosition.strategy_id == strategy_id,
            StrategyPosition.symbol == symbol,
        )
    )
    return pos.qty if pos else 0.0


def _ensure_leg(
    held: float,
    symbol: str,
    intent: str | None,
    side: str | None,
    qty: float,
) -> None:
    closing_long = intent == "sell_to_close" or (intent is None and side == "sell")
    closing_short = intent == "buy_to_close"
    if closing_long and held + 1e-9 < qty:
        raise AppError(400, f"not enough {symbol} position to sell {qty:g} (held {held:g})")
    if closing_short and held - 1e-9 > -qty:
        raise AppError(400, f"not enough short {symbol} to cover {qty:g} (held {held:g})")


def _apply_simple(db: Session, order: Order, remote: BrokerOrder) -> None:
    strategy = db.get(Strategy, order.strategy_id)
    if strategy is None or order.symbol is None or order.side is None:
        return
    multiplier = OPTION_MULTIPLIER if order.asset_class == "us_option" else 1.0
    new_qty = float(remote.filled_qty or 0)
    new_avg = float(remote.filled_avg_price or 0)
    new_notional = new_avg * new_qty * multiplier
    delta_qty = new_qty - float(order.filled_qty or 0)
    delta_notional = new_notional - float(order.applied_notional or 0)
    order.filled_qty = new_qty
    order.filled_avg_price = remote.filled_avg_price
    order.applied_notional = new_notional
    if abs(delta_qty) < 1e-9 and abs(delta_notional) < 1e-6:
        return
    price = (delta_notional / (delta_qty * multiplier)) if abs(delta_qty) > 1e-9 else new_avg
    signed = delta_qty if order.side == "buy" else -delta_qty
    cash_delta = -delta_notional if order.side == "buy" else delta_notional
    strategy.cash_remaining = round(strategy.cash_remaining + cash_delta, 4)
    if abs(delta_qty) > 1e-9:
        _apply_position(db, strategy, order.symbol, order.asset_class, signed, price)
        db.add(
            Fill(
                order_id=order.id,
                strategy_id=order.strategy_id,
                symbol=order.symbol,
                side=order.side,
                qty=abs(delta_qty),
                price=price,
                filled_at=datetime.now(timezone.utc),
            )
        )


def _apply_mleg(db: Session, order: Order, remote: BrokerOrder) -> None:
    strategy = db.get(Strategy, order.strategy_id)
    if strategy is None:
        return
    new_qty = float(remote.filled_qty or 0)
    new_avg = float(remote.filled_avg_price or 0)
    new_notional = new_avg * new_qty * OPTION_MULTIPLIER
    delta_notional = new_notional - float(order.applied_notional or 0)
    order.filled_qty = new_qty
    order.filled_avg_price = remote.filled_avg_price
    order.applied_notional = new_notional
    if abs(delta_notional) >= 1e-6:
        strategy.cash_remaining = round(strategy.cash_remaining - delta_notional, 4)

    for remote_leg in remote.legs or []:
        leg = _match_leg(order, remote_leg)
        if leg is None:
            continue
        leg_qty = float(remote_leg.filled_qty or 0)
        leg_avg = float(remote_leg.filled_avg_price or 0)
        leg_notional = leg_avg * leg_qty * OPTION_MULTIPLIER
        delta_qty = leg_qty - float(leg.filled_qty or 0)
        previous_notional = float(leg.applied_notional or 0)
        leg.filled_qty = leg_qty
        leg.filled_avg_price = remote_leg.filled_avg_price
        leg.applied_notional = leg_notional
        if abs(delta_qty) < 1e-9:
            continue
        price = (leg_notional - previous_notional) / (delta_qty * OPTION_MULTIPLIER)
        signed = delta_qty if leg.side == "buy" else -delta_qty
        _apply_position(db, strategy, leg.symbol, "us_option", signed, price)
        db.add(
            Fill(
                order_id=order.id,
                strategy_id=order.strategy_id,
                symbol=leg.symbol,
                side=leg.side,
                qty=abs(delta_qty),
                price=price,
                filled_at=datetime.now(timezone.utc),
            )
        )


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
    signed_qty: float,
    price: float,
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
            qty=0.0,
            avg_price=0.0,
        )
        db.add(pos)
    multiplier = OPTION_MULTIPLIER if asset_class == "us_option" else 1.0
    old = pos.qty
    new = old + signed_qty
    if abs(new) < 1e-8:
        new = 0.0
    if old == 0 or old * signed_qty > 0:
        gross = abs(old) * pos.avg_price + abs(signed_qty) * price
        pos.avg_price = round(gross / abs(new), 6) if new else 0.0
    elif new == 0 or old * new > 0:
        closed = abs(signed_qty)
        strategy.realized_pnl = round(strategy.realized_pnl + _realized(old, pos.avg_price, price, closed, multiplier), 4)
    else:
        closed = abs(old)
        strategy.realized_pnl = round(strategy.realized_pnl + _realized(old, pos.avg_price, price, closed, multiplier), 4)
        pos.avg_price = round(price, 6)
    pos.qty = new


def _realized(old_qty: float, avg: float, price: float, closed: float, multiplier: float) -> float:
    if old_qty > 0:
        return (price - avg) * closed * multiplier
    return (avg - price) * closed * multiplier
