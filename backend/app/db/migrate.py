"""Lightweight auto-migration for additive schema changes.

`create_all()` only creates *new* tables — it never alters existing ones. So
when a model gains a new column, an older database would be missing it and
queries break. This scans each existing table and adds any missing columns via
`ALTER TABLE ... ADD COLUMN` (the common, safe evolution). It deliberately does
NOT drop or retype columns. Runs on every startup; it's a no-op once in sync.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.models import Base

log = logging.getLogger("hub.migrate")


def _literal(v: object) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def _type_default(coltype: object) -> object:
    name = coltype.__class__.__name__.lower()
    if "bool" in name:
        return False
    if "int" in name:
        return 0
    if "float" in name or "numeric" in name or "real" in name:
        return 0
    if "json" in name:
        return "{}"
    if "date" in name or "time" in name:
        return None  # leave NULL; usually nullable anyway
    return ""  # string / text


def _add_column_sql(table_name: str, col, dialect) -> str:
    coltype = col.type.compile(dialect)
    sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {coltype}'
    default = None
    d = col.default
    if d is not None and getattr(d, "is_scalar", False):
        default = d.arg
    if default is None and not col.nullable:
        default = _type_default(col.type)
    if default is not None:
        sql += f" DEFAULT {_literal(default)}"
    return sql


def auto_migrate(engine: Engine) -> list[str]:
    """Add any columns present in the models but missing in the DB. Returns the
    list of "table.column" added (empty when already in sync)."""
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue  # brand-new table: create_all already handled it
            db_cols = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in db_cols:
                    continue
                sql = _add_column_sql(table.name, col, engine.dialect)
                try:
                    conn.execute(text(sql))
                    added.append(f"{table.name}.{col.name}")
                except Exception as e:  # noqa: BLE001 — one bad column shouldn't abort startup
                    log.warning("auto-migrate: could not add %s.%s: %s", table.name, col.name, e)
    if added:
        log.info("auto-migrate added columns: %s", ", ".join(added))
    return added
