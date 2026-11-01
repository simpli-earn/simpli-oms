"""Paper trading for Kalshi and Polymarket against their live order books.

An order walks the current book at submit time, the same way a taker order would on the exchange.
Whatever a limit order cannot fill rests here, and the sync loop fills it once the live book trades
through its price. Positions pay $1 per winning contract when the market resolves.

Each strategy has its own prediction-market cash (pm_accounts), separate from its Alpaca budget.
Kalshi taker fees use Kalshi's formula, 7% x contracts x price x (1 - price) rounded up to the cent.
Resting (maker) fills and Polymarket fills are free.
"""

from __future__ import annotations

import logging
import math
import uuid
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models import PmAccount, PmFill, PmOrder, PmPosition, Strategy, utcnow
from app.services.predictions import PredictionError, Predictions

logger = logging.getLogger(__name__)

D = Decimal
ZERO = D("0")
ONE = D("1")
PLACES = D("0.0000000001")
VENUES = ("kalshi", "polymarket")
KALSHI_TAKER_RATE = D("0.07")
NOT_READY = (
    "Prediction paper trading tables are not set up in the shared database yet. "
    "A Supabase admin needs to run backend/supabase_predictions.sql once."
)


class PaperError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def q(value: Decimal) -> Decimal:
    return value.quantize(PLACES, rounding=ROUND_HALF_UP)


def taker_fee(venue: str, fills: list[tuple[Decimal, Decimal]]) -> Decimal:
    if venue != "kalshi" or not fills:
        return ZERO
    raw = sum(KALSHI_TAKER_RATE * size * price * (ONE - price) for price, size in fills)
    return D(math.ceil(raw * 100 - D("0.0000001"))) / 100


def levels(book: dict, outcome: str, action: str) -> list[tuple[Decimal, Decimal]]:
    """Book levels a taker trades against, best first, priced for the chosen outcome.

    The book is the YES side. A YES bid at p is a NO offer at 1 - p, and a YES offer at p is a NO bid at 1 - p.
    """
    bids = [(D(str(p)), D(str(s))) for p, s in book.get("bids") or []]
    asks = [(D(str(p)), D(str(s))) for p, s in book.get("asks") or []]
    if outcome == "yes":
        side = asks if action == "buy" else bids
    else:
        side = [(ONE - p, s) for p, s in (bids if action == "buy" else asks)]
    return sorted(side, key=lambda level: level[0], reverse=action == "sell")


def walk(book_levels: list[tuple[Decimal, Decimal]], count: Decimal, limit: Decimal | None, action: str,
         whole: bool = False):
    """Take liquidity best price first. whole=True fills whole contracts only (Kalshi)."""
    fills: list[tuple[Decimal, Decimal]] = []
    left = count
    for price, size in book_levels:
        if left <= 0:
            break
        if limit is not None and (price > limit if action == "buy" else price < limit):
            break
        take = min(left, size.to_integral_value(ROUND_FLOOR) if whole else size)
        if take > 0:
            fills.append((price, take))
            left -= take
    return fills


class Paper:
    def __init__(self, predictions: Predictions):
        self.predictions = predictions
        self._ready = False

    # ---------- setup ----------

    def ensure_ready(self, db: Session) -> None:
        if self._ready:
            return
        try:
            db.execute(select(PmAccount.strategy_id).limit(1))
        except (ProgrammingError, OperationalError) as exc:
            db.rollback()
            raise PaperError(503, NOT_READY) from exc
        self._ready = True

    def is_ready(self, db: Session) -> bool:
        try:
            self.ensure_ready(db)
        except PaperError:
            return False
        return True

    # ---------- accounts ----------

    def create_account(self, db: Session, strategy_id: str, cash_budget: float) -> dict:
        self.ensure_ready(db)
        if db.get(Strategy, strategy_id) is None:
            raise PaperError(404, f"strategy {strategy_id!r} is not registered. Register it on the Strategies page first.")
        if db.get(PmAccount, strategy_id) is not None:
            raise PaperError(409, "this strategy already has a prediction-market paper account")
        budget = q(D(str(cash_budget)))
        account = PmAccount(strategy_id=strategy_id, cash_budget=budget, cash=budget, cash_reserved=ZERO,
                            realized_pnl=ZERO, fees_paid=ZERO)
        db.add(account)
        db.commit()
        return self.account_out(db, account)

    def deposit(self, db: Session, strategy_id: str, amount: float) -> dict:
        self.ensure_ready(db)
        account = self._account(db, strategy_id)
        value = q(D(str(amount)))
        account.cash_budget += value
        account.cash += value
        db.commit()
        return self.account_out(db, account)

    def accounts(self, db: Session) -> list[dict]:
        self.ensure_ready(db)
        return [self.account_out(db, a) for a in db.scalars(select(PmAccount).order_by(PmAccount.strategy_id))]

    def _account(self, db: Session, strategy_id: str, lock: bool = False) -> PmAccount:
        account = db.get(PmAccount, strategy_id, with_for_update=lock)
        if account is None:
            raise PaperError(404, f"{strategy_id!r} has no prediction-market paper account. Fund one first.")
        return account

    # ---------- orders ----------

    def place(self, db: Session, body: dict) -> dict:
        self.ensure_ready(db)
        sid = body["strategy_id"]
        coid = body.get("client_order_id")
        if coid:
            existing = db.scalar(select(PmOrder).where(PmOrder.strategy_id == sid, PmOrder.client_order_id == coid))
            if existing is not None:
                return self.order_out(existing)
        venue, market_id = body["venue"], body["market_id"].strip()
        outcome, action, kind = body["outcome"], body["action"], body.get("type", "limit")
        tif = "ioc" if kind == "market" else body.get("time_in_force", "gtc")
        count = D(str(body["count"]))
        limit = D(str(body["limit_price"])) if body.get("limit_price") is not None else None
        if venue not in VENUES:
            raise PaperError(400, f"paper trading supports {', '.join(VENUES)}")
        if venue == "kalshi" and count != count.to_integral_value():
            raise PaperError(400, "Kalshi contracts are whole numbers")
        if kind == "limit" and (limit is None or not ZERO < limit < ONE):
            raise PaperError(400, "a limit order needs limit_price between 0 and 1")
        if kind == "market":
            limit = None

        try:
            market = self.predictions.snapshot(venue, market_id)
        except PredictionError as exc:
            raise PaperError(exc.status, exc.detail) from exc

        account = self._account(db, sid, lock=True)
        held = self._sellable(db, sid, venue, market_id, outcome) if action == "sell" else ZERO
        order = PmOrder(
            id=str(uuid.uuid4()), strategy_id=sid, client_order_id=coid, venue=venue, market_id=market_id,
            title=_title(market), outcome=outcome, action=action, type=kind, count=count, limit_price=limit,
            status="open", filled_count=ZERO, fees=ZERO, reserved=ZERO,
        )
        db.add(order)
        db.flush()  # fills reference the order

        reason = None
        if market.get("status") not in ("active", "open") or market.get("result"):
            reason = f"market is {market.get('status') or 'closed'}"
        fills = walk(levels(market["book"], outcome, action), count, limit, action, whole=venue == "kalshi")
        fee = taker_fee(venue, fills)
        filled = sum((s for _, s in fills), ZERO)
        rest = count - filled if tif == "gtc" else ZERO
        if reason is None and action == "sell":
            if count > held:
                reason = f"only {_num(held)} {outcome.upper()} contracts are available to sell"
        if reason is None and action == "buy":
            need = sum((p * s for p, s in fills), ZERO) + fee + (rest * limit if limit is not None else ZERO)
            if need > account.cash:
                reason = f"needs ${need:.2f}, strategy has ${account.cash:.2f} spendable"
        if reason:
            order.status, order.reject_reason = "rejected", reason
            db.commit()
            return self.order_out(order)

        if fills:
            self._apply(db, account, order, fills, fee)
        if rest > 0:
            if action == "buy":
                order.reserved = q(rest * limit)
                account.cash -= order.reserved
                account.cash_reserved += order.reserved
        elif order.filled_count >= count:
            order.status = "filled"
        else:
            order.status = "canceled"
            order.reject_reason = (
                "no liquidity at or better than the limit" if filled == 0 else "book exhausted, rest canceled"
            )
        order.updated_at = utcnow()
        db.commit()
        return self.order_out(order)

    def cancel(self, db: Session, order_id: str) -> dict:
        self.ensure_ready(db)
        order = db.get(PmOrder, order_id)
        if order is None:
            raise PaperError(404, "order not found")
        if order.status != "open":
            raise PaperError(409, f"order is {order.status}")
        account = self._account(db, order.strategy_id, lock=True)
        self._close(account, order, "canceled", None)
        db.commit()
        return self.order_out(order)

    def orders(self, db: Session, strategy_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
        self.ensure_ready(db)
        stmt = select(PmOrder).order_by(PmOrder.created_at.desc()).limit(min(max(limit, 1), 500))
        if strategy_id:
            stmt = stmt.where(PmOrder.strategy_id == strategy_id)
        if status:
            stmt = stmt.where(PmOrder.status == status)
        return [self.order_out(o) for o in db.scalars(stmt)]

    def _sellable(self, db: Session, sid: str, venue: str, market_id: str, outcome: str) -> Decimal:
        position = self._position(db, sid, venue, market_id, outcome, create=False)
        held = position.count if position else ZERO
        pending = db.scalars(select(PmOrder).where(
            PmOrder.strategy_id == sid, PmOrder.venue == venue, PmOrder.market_id == market_id,
            PmOrder.outcome == outcome, PmOrder.action == "sell", PmOrder.status == "open",
        ))
        return held - sum((o.count - o.filled_count for o in pending), ZERO)

    def _position(self, db, sid, venue, market_id, outcome, create=True, title="") -> PmPosition | None:
        position = db.scalar(select(PmPosition).where(
            PmPosition.strategy_id == sid, PmPosition.venue == venue,
            PmPosition.market_id == market_id, PmPosition.outcome == outcome,
        ))
        if position is None and create:
            position = PmPosition(strategy_id=sid, venue=venue, market_id=market_id, outcome=outcome, title=title,
                                  count=ZERO, avg_price=ZERO, realized_pnl=ZERO)
            db.add(position)
        return position

    def _apply(self, db, account: PmAccount, order: PmOrder, fills, fee: Decimal) -> None:
        size = sum((s for _, s in fills), ZERO)
        notional = sum((p * s for p, s in fills), ZERO)
        price = q(notional / size)
        position = self._position(db, order.strategy_id, order.venue, order.market_id, order.outcome, title=order.title)
        if order.action == "buy":
            if order.reserved > 0:  # this part of a resting buy was paid for at the limit when it was placed
                release = min(order.reserved, q(size * order.limit_price))
                order.reserved -= release
                account.cash_reserved -= release
                account.cash += release
            account.cash -= q(notional + fee)
            position.avg_price = q((position.count * position.avg_price + notional) / (position.count + size))
            position.count += size
        else:
            account.cash += q(notional - fee)
            pnl = q(notional - size * position.avg_price)
            position.realized_pnl += pnl
            account.realized_pnl += pnl
            position.count -= size
        position.updated_at = utcnow()
        account.fees_paid += fee
        old = order.filled_count
        order.filled_count = old + size
        order.avg_price = q(((order.avg_price or ZERO) * old + notional) / order.filled_count)
        order.fees += fee
        db.add(PmFill(order_id=order.id, strategy_id=order.strategy_id, venue=order.venue, market_id=order.market_id,
                      outcome=order.outcome, action=order.action, count=size, price=price, fee=fee))

    def _close(self, account: PmAccount, order: PmOrder, status: str, reason: str | None) -> None:
        if order.reserved > 0:
            account.cash_reserved -= order.reserved
            account.cash += order.reserved
            order.reserved = ZERO
        order.status = status
        order.reject_reason = reason
        order.updated_at = utcnow()

    # ---------- background sync ----------

    def sync_once(self, session_factory) -> None:
        with session_factory() as db:
            if not self.is_ready(db):
                return
            open_orders = list(db.scalars(select(PmOrder).where(PmOrder.status == "open")))
            held = db.execute(select(PmPosition.venue, PmPosition.market_id).where(PmPosition.count > 0).distinct()).all()
        markets = {(o.venue, o.market_id) for o in open_orders} | {tuple(row) for row in held}
        for venue, market_id in markets:
            try:
                market = self.predictions.snapshot(venue, market_id)
            except PredictionError as exc:
                logger.warning("paper sync: %s %s unavailable: %s", venue, market_id, exc.detail)
                continue
            with session_factory() as db:
                try:
                    self._sync_market(db, venue, market_id, market)
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("paper sync failed for %s %s", venue, market_id)

    def _sync_market(self, db: Session, venue: str, market_id: str, market: dict) -> None:
        result = market.get("result")
        closed = market.get("status") not in ("active", "open") or bool(result)
        orders = db.scalars(select(PmOrder).where(
            PmOrder.venue == venue, PmOrder.market_id == market_id, PmOrder.status == "open",
        ).order_by(PmOrder.created_at))
        for order in orders:
            account = self._account(db, order.strategy_id, lock=True)
            if closed:
                self._close(account, order, "canceled", "market closed")
                continue
            fills = walk(levels(market["book"], order.outcome, order.action),
                         order.count - order.filled_count, order.limit_price, order.action, whole=venue == "kalshi")
            if order.action == "sell":
                position = self._position(db, order.strategy_id, venue, market_id, order.outcome, create=False)
                cap = position.count if position else ZERO
                fills = walk(fills, min(order.count - order.filled_count, cap), None, "sell")
            if fills:
                self._apply(db, account, order, fills, ZERO)  # a resting order is the maker
                if order.filled_count >= order.count:
                    self._close(account, order, "filled", None)
                order.updated_at = utcnow()
        if result in ("yes", "no"):
            self._settle(db, venue, market_id, result)

    def _settle(self, db: Session, venue: str, market_id: str, result: str) -> None:
        positions = db.scalars(select(PmPosition).where(
            PmPosition.venue == venue, PmPosition.market_id == market_id, PmPosition.count > 0,
        ))
        for position in positions:
            account = self._account(db, position.strategy_id, lock=True)
            price = ONE if position.outcome == result else ZERO
            payout = q(position.count * price)
            pnl = q(payout - position.count * position.avg_price)
            account.cash += payout
            account.realized_pnl += pnl
            position.realized_pnl += pnl
            db.add(PmFill(order_id=None, strategy_id=position.strategy_id, venue=venue, market_id=market_id,
                          outcome=position.outcome, action="settle", count=position.count, price=price, fee=ZERO))
            position.count = ZERO
            position.updated_at = utcnow()

    # ---------- reads ----------

    def book(self, db: Session, strategy_id: str) -> dict:
        self.ensure_ready(db)
        account = self._account(db, strategy_id)
        positions = db.scalars(select(PmPosition).where(PmPosition.strategy_id == strategy_id, PmPosition.count > 0))
        orders = db.scalars(select(PmOrder).where(PmOrder.strategy_id == strategy_id)
                            .order_by(PmOrder.created_at.desc()).limit(100))
        fills = db.scalars(select(PmFill).where(PmFill.strategy_id == strategy_id)
                           .order_by(PmFill.filled_at.desc()).limit(100))
        return {
            "account": self.account_out(db, account),
            "positions": [self.position_out(p) for p in positions],
            "orders": [self.order_out(o) for o in orders],
            "fills": [self.fill_out(f) for f in fills],
        }

    def mark(self, venue: str, market_id: str, outcome: str) -> float | None:
        """Mid price for the outcome, or the last trade when one side of the book is empty."""
        try:
            quote = self.predictions.quote(venue, market_id)
        except PredictionError:
            return None
        if quote.get("result") in ("yes", "no"):
            yes = 1.0 if quote["result"] == "yes" else 0.0
        elif quote.get("yes_bid") and quote.get("yes_ask"):
            yes = (quote["yes_bid"] + quote["yes_ask"]) / 2
        else:
            yes = quote.get("last")
        if yes is None:
            return None
        return round(yes if outcome == "yes" else 1 - yes, 6)

    def position_out(self, p: PmPosition) -> dict:
        mark = self.mark(p.venue, p.market_id, p.outcome)
        count, avg = float(p.count), float(p.avg_price)
        return {
            "strategy_id": p.strategy_id, "venue": p.venue, "market_id": p.market_id, "outcome": p.outcome,
            "title": p.title, "count": count, "avg_price": avg, "mark": mark,
            "market_value": None if mark is None else round(count * mark, 4),
            "unrealized_pnl": None if mark is None else round(count * (mark - avg), 4),
            "realized_pnl": float(p.realized_pnl),
        }

    def account_out(self, db: Session, a: PmAccount) -> dict:
        positions = [self.position_out(p) for p in db.scalars(
            select(PmPosition).where(PmPosition.strategy_id == a.strategy_id, PmPosition.count > 0))]
        value = sum(p["market_value"] or 0 for p in positions)
        unrealized = sum(p["unrealized_pnl"] or 0 for p in positions)
        equity = float(a.cash + a.cash_reserved) + value
        return {
            "strategy_id": a.strategy_id, "cash_budget": float(a.cash_budget), "cash": float(a.cash),
            "cash_reserved": float(a.cash_reserved), "positions_value": round(value, 4), "equity": round(equity, 4),
            "realized_pnl": float(a.realized_pnl), "unrealized_pnl": round(unrealized, 4), "fees_paid": float(a.fees_paid),
            "total_pnl": round(equity - float(a.cash_budget), 4), "open_positions": len(positions),
            "created_at": a.created_at.isoformat(),
        }

    @staticmethod
    def order_out(o: PmOrder) -> dict:
        return {
            "id": o.id, "strategy_id": o.strategy_id, "client_order_id": o.client_order_id, "venue": o.venue,
            "market_id": o.market_id, "title": o.title, "outcome": o.outcome, "action": o.action, "type": o.type,
            "count": float(o.count), "limit_price": None if o.limit_price is None else float(o.limit_price),
            "status": o.status, "filled_count": float(o.filled_count),
            "avg_price": None if o.avg_price is None else float(o.avg_price), "fees": float(o.fees),
            "reject_reason": o.reject_reason, "created_at": o.created_at.isoformat(),
            "updated_at": o.updated_at.isoformat(),
        }

    @staticmethod
    def fill_out(f: PmFill) -> dict:
        return {
            "id": f.id, "order_id": f.order_id, "strategy_id": f.strategy_id, "venue": f.venue,
            "market_id": f.market_id, "outcome": f.outcome, "action": f.action, "count": float(f.count),
            "price": float(f.price), "fee": float(f.fee), "filled_at": f.filled_at.isoformat(),
        }


def _title(market: dict) -> str:
    title, sub = market.get("title") or "", market.get("subtitle") or ""
    return (f"{title} · {sub}" if sub else title)[:500]


def _num(value: Decimal) -> str:
    return f"{value.normalize():f}"
