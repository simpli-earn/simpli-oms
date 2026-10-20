import hashlib
import hmac

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session


def get_db(request: Request):
    db: Session = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_broker(request: Request):
    return request.app.state.broker


def require_api_key(
    request: Request,
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> None:
    expected = request.app.state.settings.oms_api_key
    if not expected or not _keys_match(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid api key")


def _keys_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )
