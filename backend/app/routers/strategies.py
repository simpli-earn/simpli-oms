from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_broker, get_db, require_api_key
from app.models import Fill, Order, Strategy
from app.present import fill_out, order_out, position_out, strategy_out
from app.schemas import BookOut, StrategyIn, StrategyOut
from app.services.ledger import unrealized_pnl
from app.services.quotes import price_map

router = APIRouter(prefix="/api/strategies", tags=["strategies"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=StrategyOut, status_code=201)
def create_strategy(body: StrategyIn, db: Session = Depends(get_db)):
    if db.get(Strategy, body.id) is not None:
        raise HTTPException(status_code=409, detail="strategy already exists")
    strategy = Strategy(id=body.id, cash_budget=body.cash_budget, cash_remaining=body.cash_budget)
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy_out(strategy, 0.0)


@router.get("", response_model=list[StrategyOut])
def list_strategies(db: Session = Depends(get_db), broker=Depends(get_broker)):
    strategies = list(
        db.scalars(select(Strategy).options(selectinload(Strategy.positions)).order_by(Strategy.created_at))
    )
    prices = price_map(broker)
    return [strategy_out(item, unrealized_pnl(item.positions, prices)) for item in strategies]


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: str, db: Session = Depends(get_db), broker=Depends(get_broker)):
    strategy = db.scalar(
        select(Strategy).where(Strategy.id == strategy_id).options(selectinload(Strategy.positions))
    )
    if strategy is None:
        raise HTTPException(status_code=404, detail="unknown strategy")
    return strategy_out(strategy, unrealized_pnl(strategy.positions, price_map(broker)))


@router.get("/{strategy_id}/book", response_model=BookOut)
def get_book(strategy_id: str, db: Session = Depends(get_db), broker=Depends(get_broker)):
    strategy = db.scalar(
        select(Strategy).where(Strategy.id == strategy_id).options(selectinload(Strategy.positions))
    )
    if strategy is None:
        raise HTTPException(status_code=404, detail="unknown strategy")
    prices = price_map(broker)
    positions = [
        position_out(pos, None if prices is None else prices.get(pos.symbol))
        for pos in strategy.positions
        if abs(pos.qty) >= 1e-8
    ]
    orders = db.scalars(
        select(Order)
        .where(Order.strategy_id == strategy_id)
        .options(selectinload(Order.legs))
        .order_by(Order.created_at.desc())
        .limit(200)
    )
    fills = db.scalars(
        select(Fill).where(Fill.strategy_id == strategy_id).order_by(Fill.filled_at.desc()).limit(200)
    )
    return BookOut(
        strategy=strategy_out(strategy, unrealized_pnl(strategy.positions, prices)),
        positions=positions,
        orders=[order_out(item) for item in orders],
        fills=[fill_out(item) for item in fills],
    )
