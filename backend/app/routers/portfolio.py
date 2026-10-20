from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_broker, get_db, require_api_key
from app.models import Fill, StrategyPosition
from app.present import fill_out
from app.schemas import AccountOut, AccountPositionOut, StrategyTrades, SymbolDetailOut
from app.services.broker import BrokerError

router = APIRouter(prefix="/api", tags=["portfolio"], dependencies=[Depends(require_api_key)])


@router.get("/account", response_model=AccountOut)
def get_account(broker=Depends(get_broker)):
    try:
        return broker.get_account()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/positions", response_model=list[AccountPositionOut])
def get_positions(broker=Depends(get_broker)):
    try:
        return broker.get_positions()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/positions/{symbol}", response_model=SymbolDetailOut)
def get_symbol(symbol: str, db: Session = Depends(get_db), broker=Depends(get_broker)):
    symbol = symbol.strip().upper()
    account_position = None
    account_error = None
    try:
        account_position = broker.get_position(symbol)
    except BrokerError as exc:
        account_error = str(exc)

    positions = list(
        db.scalars(select(StrategyPosition).where(StrategyPosition.symbol == symbol)).all()
    )
    grouped = []
    for pos in positions:
        trades = db.scalars(
            select(Fill)
            .where(Fill.strategy_id == pos.strategy_id, Fill.symbol == symbol)
            .order_by(Fill.filled_at.desc())
            .limit(200)
        )
        grouped.append(
            StrategyTrades(
                strategy_id=pos.strategy_id,
                qty=pos.qty,
                avg_price=pos.avg_price,
                asset_class=pos.asset_class,
                trades=[fill_out(item) for item in trades],
            )
        )
    return SymbolDetailOut(
        symbol=symbol,
        account_position=account_position,
        account_error=account_error,
        strategies=grouped,
    )
