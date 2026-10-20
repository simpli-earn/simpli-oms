from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.deps import get_broker, get_db, require_api_key
from app.errors import AppError
from app.present import order_out
from app.schemas import OrderIn, OrderOut
from app.services.orders import cancel_order, get_order, list_orders, place_order

router = APIRouter(prefix="/api/orders", tags=["orders"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=OrderOut)
def create_order(
    body: OrderIn,
    response: Response,
    db: Session = Depends(get_db),
    broker=Depends(get_broker),
):
    try:
        order, created = place_order(db, broker, body)
    except AppError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    response.status_code = 201 if created else 200
    return order_out(order)


@router.get("", response_model=list[OrderOut])
def get_orders(strategy_id: str | None = None, db: Session = Depends(get_db)):
    return [order_out(item) for item in list_orders(db, strategy_id)]


@router.get("/{order_id}", response_model=OrderOut)
def read_order(order_id: str, db: Session = Depends(get_db)):
    try:
        return order_out(get_order(db, order_id))
    except AppError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.delete("/{order_id}", response_model=OrderOut)
def delete_order(order_id: str, db: Session = Depends(get_db), broker=Depends(get_broker)):
    try:
        return order_out(cancel_order(db, broker, order_id))
    except AppError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
