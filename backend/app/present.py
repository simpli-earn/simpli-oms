from app.models import Fill, Order, Strategy, StrategyPosition
from app.schemas import FillOut, LegOut, OrderOut, PositionOut, StrategyOut


def strategy_out(strategy: Strategy, unrealized: float | None = None) -> StrategyOut:
    return StrategyOut(
        id=strategy.id,
        cash_budget=strategy.cash_budget,
        cash_remaining=strategy.cash_remaining,
        realized_pnl=strategy.realized_pnl,
        unrealized_pnl=unrealized,
        created_at=strategy.created_at,
    )


def order_out(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id,
        strategy_id=order.strategy_id,
        client_order_id=order.client_order_id,
        alpaca_order_id=order.alpaca_order_id,
        asset_class=order.asset_class,
        order_class=order.order_class,
        symbol=order.symbol,
        side=order.side,
        type=order.type,
        qty=order.qty,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        time_in_force=order.time_in_force,
        position_intent=order.position_intent,
        status=order.status,
        filled_qty=order.filled_qty,
        filled_avg_price=order.filled_avg_price,
        reject_reason=order.reject_reason,
        legs=[
            LegOut(
                symbol=leg.symbol,
                ratio_qty=leg.ratio_qty,
                side=leg.side,
                position_intent=leg.position_intent,
                filled_qty=leg.filled_qty,
                filled_avg_price=leg.filled_avg_price,
            )
            for leg in order.legs
        ],
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def fill_out(fill: Fill) -> FillOut:
    return FillOut(
        id=fill.id,
        order_id=fill.order_id,
        strategy_id=fill.strategy_id,
        symbol=fill.symbol,
        side=fill.side,
        qty=fill.qty,
        price=fill.price,
        filled_at=fill.filled_at,
    )


def position_out(position: StrategyPosition, price: float | None = None) -> PositionOut:
    unrealized = None
    if price is not None and abs(position.qty) >= 1e-8:
        multiplier = 100.0 if position.asset_class == "us_option" else 1.0
        unrealized = round((price - position.avg_price) * position.qty * multiplier, 4)
    return PositionOut(
        strategy_id=position.strategy_id,
        symbol=position.symbol,
        asset_class=position.asset_class,
        qty=position.qty,
        avg_price=position.avg_price,
        unrealized_pnl=unrealized,
    )
