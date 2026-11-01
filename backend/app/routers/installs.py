"""Which laptops have each strategy set up in SimpliEarn Desk, and how it runs there.

Each laptop reports its strategies (PUT /api/installs/{host}) while the app is open and after each
run; the strategy page reads them back (GET /api/strategies/{id}/installs).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.deps import get_db, require_api_key
from app.models import Strategy, StrategyInstall, utcnow
from app.schemas import InstallOut, InstallsIn


def require_installs_table(request: Request, db: Session = Depends(get_db)) -> None:
    """503 with a clear fix until a project owner has run add_strategy_installs.sql."""
    if getattr(request.app.state, "installs_table_ready", False):
        return
    if "strategy_installs" not in inspect(db.get_bind()).get_table_names():
        raise HTTPException(
            status_code=503,
            detail="the installs table is missing: a Supabase project owner needs to run backend/add_strategy_installs.sql",
        )
    request.app.state.installs_table_ready = True


router = APIRouter(
    prefix="/api",
    tags=["strategy installs"],
    dependencies=[Depends(require_api_key), Depends(require_installs_table)],
)


@router.put("/installs/{host}", status_code=204)
def report_installs(host: str, body: InstallsIn, db: Session = Depends(get_db)):
    if not host or len(host) > 128:
        raise HTTPException(status_code=422, detail="bad host name")
    now = utcnow()
    known = set(db.scalars(select(Strategy.id)))
    rows = {r.strategy_id: r for r in db.scalars(select(StrategyInstall).where(StrategyInstall.host == host))}
    reported = set()
    for item in body.strategies:
        if item.strategy_id not in known:  # set up locally but not registered in the OMS
            continue
        reported.add(item.strategy_id)
        row = rows.get(item.strategy_id) or StrategyInstall(strategy_id=item.strategy_id, host=host)
        row.installed, row.version, row.schedule = True, item.version, item.schedule
        row.last_run_at, row.last_run_status = item.last_run_at, item.last_run_status
        if body.app_open or row.seen_at is None:
            row.seen_at = now
        db.add(row)
    for sid, row in rows.items():
        if sid not in reported and row.installed:
            row.installed, row.seen_at = False, now
    db.commit()


@router.get("/strategies/{strategy_id}/installs", response_model=list[InstallOut])
def list_installs(strategy_id: str, db: Session = Depends(get_db)):
    return db.scalars(
        select(StrategyInstall)
        .where(StrategyInstall.strategy_id == strategy_id, StrategyInstall.installed.is_(True))
        .order_by(StrategyInstall.seen_at.desc())
    ).all()
