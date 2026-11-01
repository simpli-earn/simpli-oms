"""Cloud copies of strategy folders, so any laptop can download and run a strategy.

Only the latest copy of each file is kept: an upload overwrites changed files and deletes
removed ones, so no history builds up. Each upload bumps the strategy's revision. An upload
from an older revision gets 409, so one laptop can't silently overwrite another's changes.
"""
import base64
import binascii
import hashlib
import posixpath

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.deps import get_db, require_api_key
from app.models import Strategy, StrategyCode, StrategyFile, utcnow
from app.schemas import CodeFileOut, CodeOut, CodeSummaryOut, CodeUploadIn

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024



def require_code_tables(request: Request, db: Session = Depends(get_db)) -> None:
    """503 with a clear fix until a project owner has run add_strategy_code.sql."""
    if getattr(request.app.state, "code_tables_ready", False):
        return
    names = set(inspect(db.get_bind()).get_table_names())
    if not {"strategy_code", "strategy_files"} <= names:
        raise HTTPException(
            status_code=503,
            detail="cloud backup tables are missing: a Supabase project owner needs to run backend/add_strategy_code.sql",
        )
    request.app.state.code_tables_ready = True


router = APIRouter(
    prefix="/api",
    tags=["strategy code"],
    dependencies=[Depends(require_api_key), Depends(require_code_tables)],
)


def _clean_path(path: str) -> str:
    clean = posixpath.normpath(path.replace("\\", "/"))
    if clean.startswith(("/", "../")) or clean in (".", "..") or "/../" in f"/{clean}/":
        raise HTTPException(status_code=422, detail=f"bad file path: {path}")
    return clean


@router.get("/strategy-code", response_model=list[CodeSummaryOut])
def list_code(db: Session = Depends(get_db)):
    totals = dict(
        (sid, (count, size))
        for sid, count, size in db.execute(
            select(StrategyFile.strategy_id, func.count(), func.coalesce(func.sum(StrategyFile.size), 0))
            .group_by(StrategyFile.strategy_id)
        )
    )
    return [
        CodeSummaryOut(
            strategy_id=row.strategy_id,
            revision=row.revision,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
            files=totals.get(row.strategy_id, (0, 0))[0],
            size=int(totals.get(row.strategy_id, (0, 0))[1]),
        )
        for row in db.scalars(select(StrategyCode).where(StrategyCode.revision > 0).order_by(StrategyCode.strategy_id))
    ]


@router.get("/strategies/{strategy_id}/code", response_model=CodeOut)
def get_code(strategy_id: str, content: bool = True, db: Session = Depends(get_db)):
    code = db.get(StrategyCode, strategy_id)
    if code is None or code.revision == 0:
        raise HTTPException(status_code=404, detail="no cloud copy of this strategy")
    return _code_out(db, code, content)


@router.put("/strategies/{strategy_id}/code", response_model=CodeOut)
def upload_code(strategy_id: str, body: CodeUploadIn, db: Session = Depends(get_db)):
    if db.get(Strategy, strategy_id) is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    decoded: dict[str, bytes] = {}
    for item in body.files:
        try:
            data = base64.b64decode(item.content_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"{item.path} is not valid base64") from exc
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{item.path} is over 10 MB")
        decoded[_clean_path(item.path)] = data
    if sum(len(d) for d in decoded.values()) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="upload is over 25 MB")
    deleted = {_clean_path(p) for p in body.deleted} - decoded.keys()

    code = db.get(StrategyCode, strategy_id, with_for_update=True)
    if code is None:
        code = StrategyCode(strategy_id=strategy_id, revision=0)
        db.add(code)
    if not body.force and body.base_revision != code.revision:
        raise HTTPException(
            status_code=409,
            detail=f"the cloud copy changed (revision {code.revision}, this laptop has {body.base_revision})",
        )
    revision = code.revision + 1
    now = utcnow()
    existing = {
        f.path: f for f in db.scalars(select(StrategyFile).where(StrategyFile.strategy_id == strategy_id))
    }
    for path, data in decoded.items():
        row = existing.get(path) or StrategyFile(strategy_id=strategy_id, path=path)
        row.content, row.sha256, row.size = data, hashlib.sha256(data).hexdigest(), len(data)
        row.revision, row.updated_at = revision, now
        db.add(row)
    for path in deleted:
        if path in existing:
            db.delete(existing[path])
    code.revision, code.updated_by, code.updated_at = revision, body.updated_by, now
    if "schedule" in body.model_fields_set:
        code.schedule = body.schedule
    try:
        db.commit()
    except ProgrammingError as exc:
        db.rollback()
        if "permission denied" not in str(exc.orig):
            raise
        raise HTTPException(
            status_code=503,
            detail="the OMS database user can't remove old cloud files yet: a Supabase project owner "
            "needs to run backend/add_strategy_code.sql again",
        ) from exc
    return _code_out(db, code, content=False)


def _code_out(db: Session, code: StrategyCode, content: bool) -> CodeOut:
    files = db.scalars(
        select(StrategyFile)
        .where(StrategyFile.strategy_id == code.strategy_id)
        .order_by(StrategyFile.path)
    )
    return CodeOut(
        strategy_id=code.strategy_id,
        revision=code.revision,
        schedule=code.schedule,
        updated_by=code.updated_by,
        updated_at=code.updated_at,
        files=[
            CodeFileOut(
                path=f.path,
                sha256=f.sha256,
                size=f.size,
                updated_at=f.updated_at,
                content_b64=base64.b64encode(f.content).decode() if content else None,
            )
            for f in files
        ],
    )
