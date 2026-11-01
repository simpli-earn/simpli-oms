from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_api_key
from app.services.paper import Paper, PaperError
from app.services.predictions import PredictionError, Predictions

router = APIRouter(prefix="/api/predictions", tags=["predictions"], dependencies=[Depends(require_api_key)])


def get_predictions(request: Request) -> Predictions:
    return request.app.state.predictions


def get_paper(request: Request) -> Paper:
    return request.app.state.paper


class PaperAccountIn(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=64)
    cash_budget: float = Field(gt=0, le=10_000_000)


class DepositIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)


class PaperOrderIn(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=64)
    venue: Literal["kalshi", "polymarket"]
    market_id: str = Field(min_length=1, max_length=128, description="Kalshi ticker or Polymarket market id")
    outcome: Literal["yes", "no"]
    action: Literal["buy", "sell"] = "buy"
    type: Literal["limit", "market"] = "limit"
    count: float = Field(gt=0, le=1_000_000)
    limit_price: float | None = Field(default=None, gt=0, lt=1, description="Dollars per contract for the chosen outcome")
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    client_order_id: str | None = Field(default=None, max_length=64)


class KalshiOrderIn(BaseModel):
    ticker: str = Field(min_length=1)
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"] = "buy"
    count: int = Field(gt=0, le=10000)
    price: float = Field(gt=0, lt=1, description="Limit price in dollars for the chosen side, 0.01 to 0.99")
    client_order_id: str | None = None
    time_in_force: Literal["good_till_canceled", "immediate_or_cancel", "fill_or_kill"] | None = None


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (PredictionError, PaperError) as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/status")
def status(p: Predictions = Depends(get_predictions), paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    """Which venues are available, whether paper trading is set up, and Kalshi demo keys."""
    return {**p.status(), "paper_ready": paper.is_ready(db)}


@router.get("/markets")
def markets(venue: str = "kalshi", q: str = "", limit: int = 50, category: str = "",
            p: Predictions = Depends(get_predictions)):
    """Open markets on kalshi, kalshi-demo, or polymarket, busiest first. Prices are YES dollars.

    category=mentions (Kalshi only) lists Kalshi's mention markets ("What will X say..."), refreshed every 10 minutes.
    """
    return _call(p.markets, venue, q, limit, category)


@router.get("/markets/{venue}/{market_id}")
def market(venue: str, market_id: str, p: Predictions = Depends(get_predictions)):
    """One market with its YES order book."""
    return _call(p.market, venue, market_id)


@router.get("/kalshi/portfolio")
def kalshi_portfolio(p: Predictions = Depends(get_predictions)):
    """Kalshi demo balance, positions, and orders."""
    return _call(p.kalshi_portfolio)


@router.post("/kalshi/orders", status_code=201)
def kalshi_order(body: KalshiOrderIn, p: Predictions = Depends(get_predictions)):
    """Send a limit order to the Kalshi demo exchange."""
    return _call(p.kalshi_order, **body.model_dump())


@router.delete("/kalshi/orders/{order_id}")
def kalshi_cancel(order_id: str, p: Predictions = Depends(get_predictions)):
    return _call(p.kalshi_cancel, order_id)


# ---------- paper trading against live books ----------


@router.get("/paper/accounts")
def paper_accounts(paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    """Every strategy's prediction-market paper account with equity and P&L."""
    return _call(paper.accounts, db)


@router.post("/paper/accounts", status_code=201)
def paper_create_account(body: PaperAccountIn, paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    """Give a registered strategy prediction-market paper cash, separate from its Alpaca budget."""
    return _call(paper.create_account, db, body.strategy_id, body.cash_budget)


@router.post("/paper/accounts/{strategy_id}/deposit")
def paper_deposit(strategy_id: str, body: DepositIn, paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    return _call(paper.deposit, db, strategy_id, body.amount)


@router.get("/paper/accounts/{strategy_id}")
def paper_book(strategy_id: str, paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    """Cash, marked positions, orders, and fills for one strategy."""
    return _call(paper.book, db, strategy_id)


@router.get("/paper/orders")
def paper_orders(strategy_id: str | None = None, status: str | None = None, limit: int = 100,
                 paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    return _call(paper.orders, db, strategy_id, status, limit)


@router.post("/paper/orders", status_code=201)
def paper_order(body: PaperOrderIn, paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    """Fill against the live book now; a limit order's remainder rests until the book trades through it.

    Repeating a client_order_id for the same strategy returns the original order.
    """
    return _call(paper.place, db, body.model_dump())


@router.delete("/paper/orders/{order_id}")
def paper_cancel(order_id: str, paper: Paper = Depends(get_paper), db: Session = Depends(get_db)):
    return _call(paper.cancel, db, order_id)
