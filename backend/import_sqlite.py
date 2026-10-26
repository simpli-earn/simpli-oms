#!/usr/bin/env python3
"""Copy a local SQLite OMS into the configured shared database.

Rows are matched by primary key. Existing shared rows are updated.
Dry-run by default:

    python import_sqlite.py
    python import_sqlite.py --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import make_engine
from app.models import Fill, Order, OrderLeg, Strategy, StrategyPosition


TABLES = (Strategy, Order, OrderLeg, Fill, StrategyPosition)
NUMERIC = {
    "cash_budget",
    "cash_remaining",
    "cash_reserved",
    "realized_pnl",
    "qty",
    "limit_price",
    "stop_price",
    "filled_qty",
    "filled_avg_price",
    "applied_notional",
    "reserved_notional",
    "ratio_qty",
    "price",
    "avg_price",
}
DEFAULTS = {
    "cash_reserved": Decimal("0"),
    "reserved_notional": Decimal("0"),
    "realized_pnl": Decimal("0"),
}


def parse_time(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_amount(value):
    if value is None:
        return None
    if isinstance(value, float):
        return Decimal(format(value, ".10f"))
    return Decimal(str(value))


def load_table(connection: sqlite3.Connection, table: str) -> list[dict]:
    cursor = connection.execute(f"select * from {table}")
    columns = [item[0] for item in cursor.description]
    loaded = []
    for row in cursor.fetchall():
        record = dict(zip(columns, row))
        if "request_payload" in record and isinstance(record["request_payload"], str):
            record["request_payload"] = json.loads(record["request_payload"])
        for column in ("created_at", "updated_at", "filled_at"):
            if column in record:
                record[column] = parse_time(record[column])
        for column, value in list(record.items()):
            if column in NUMERIC:
                record[column] = parse_amount(value)
        loaded.append(record)
    return loaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a local SQLite OMS into shared Postgres")
    parser.add_argument("--source", default="./data/oms.db", help="path to the SQLite database")
    parser.add_argument("--target", help="target database URL; defaults to OMS_DATABASE_URL in .env")
    parser.add_argument("--apply", action="store_true", help="commit the import")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.is_file():
        parser.error(f"source database does not exist: {source_path}")
    target_url = args.target or Settings().oms_database_url
    if target_url.startswith("sqlite"):
        parser.error("target must be the shared Postgres database, not SQLite")

    source = sqlite3.connect(source_path)
    target_engine = make_engine(target_url)
    counts: dict[str, int] = {}

    with Session(target_engine) as target:
        for model in TABLES:
            records = load_table(source, model.__tablename__)
            counts[model.__tablename__] = len(records)
            columns = {column.key for column in model.__mapper__.column_attrs}
            for record in records:
                values = {key: record[key] for key in columns if key in record}
                for key, value in DEFAULTS.items():
                    if key in columns and key not in values:
                        values[key] = value
                target.merge(model(**values))
            target.flush()
        if target_engine.dialect.name == "postgresql":
            for table in ("order_legs", "fills", "strategy_positions", "ledger_entries"):
                target.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence('"
                        + table
                        + "', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM "
                        + table
                    )
                )
        if args.apply:
            target.commit()
        else:
            target.rollback()

    mode = "imported" if args.apply else "would import"
    print(mode + ":")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    if not args.apply:
        print("dry run only; run again with --apply to commit")


if __name__ == "__main__":
    main()
