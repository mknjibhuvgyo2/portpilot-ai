"""Usage & cost statistics API (reads the cumulative UsageStat table)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.billing.pricing import get_pricing, set_pricing
from app.db.models import ApiKey, PortService, UsageStat
from app.db.session import get_db

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _since_day(rng: str) -> str | None:
    today = datetime.now(timezone.utc).date()
    if rng == "today":
        return today.strftime("%Y-%m-%d")
    if rng == "7d":
        return (today - timedelta(days=6)).strftime("%Y-%m-%d")
    if rng == "30d":
        return (today - timedelta(days=29)).strftime("%Y-%m-%d")
    return None  # all


@router.get("/summary")
def summary(rng: str = "7d", db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    since = _since_day(rng)

    def agg(*group):
        # Port/model summary uses only port-level rows (api_key_id IS NULL),
        # written by the in-port app with real (non-streaming) token counts.
        # Gateway key-level rows (api_key_id NOT NULL) are reported separately,
        # so totals are never double-counted.
        q = db.query(
            *group,
            func.coalesce(func.sum(UsageStat.requests), 0),
            func.coalesce(func.sum(UsageStat.errors), 0),
            func.coalesce(func.sum(UsageStat.prompt_tokens), 0),
            func.coalesce(func.sum(UsageStat.completion_tokens), 0),
            func.coalesce(func.sum(UsageStat.cost), 0.0),
        ).filter(UsageStat.api_key_id.is_(None))
        if since:
            q = q.filter(UsageStat.day >= since)
        return q

    # totals
    tot = agg().one()
    totals = {
        "requests": int(tot[0]), "errors": int(tot[1]),
        "prompt_tokens": int(tot[2]), "completion_tokens": int(tot[3]),
        "total_tokens": int(tot[2]) + int(tot[3]), "cost": round(float(tot[4]), 4),
    }

    # by port
    port_names = {p.id: p.name for p in db.query(PortService).all()}
    by_port = []
    for row in agg(UsageStat.port_id).group_by(UsageStat.port_id).all():
        pid = row[0]
        by_port.append({
            "port_id": pid, "name": port_names.get(pid, f"#{pid}" if pid else "—"),
            "requests": int(row[1]), "errors": int(row[2]),
            "prompt_tokens": int(row[3]), "completion_tokens": int(row[4]),
            "total_tokens": int(row[3]) + int(row[4]), "cost": round(float(row[5]), 4),
        })
    by_port.sort(key=lambda x: x["cost"], reverse=True)

    # by model
    by_model = []
    for row in agg(UsageStat.model).group_by(UsageStat.model).all():
        by_model.append({
            "model": row[0] or "—", "requests": int(row[1]), "errors": int(row[2]),
            "prompt_tokens": int(row[3]), "completion_tokens": int(row[4]),
            "total_tokens": int(row[3]) + int(row[4]), "cost": round(float(row[5]), 4),
        })
    by_model.sort(key=lambda x: x["cost"], reverse=True)

    # daily trend
    daily = []
    for row in agg(UsageStat.day).group_by(UsageStat.day).order_by(UsageStat.day).all():
        daily.append({
            "day": row[0], "requests": int(row[1]),
            "total_tokens": int(row[3]) + int(row[4]), "cost": round(float(row[5]), 4),
        })

    pricing = get_pricing(db)
    return {
        "range": rng, "currency": pricing.get("currency", "USD"),
        "totals": totals, "by_port": by_port, "by_model": by_model, "daily": daily,
    }


@router.get("/keys")
def key_usage(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    """Per-key cumulative cost (joined from UsageStat) alongside quota/used."""
    port_names = {p.id: p.name for p in db.query(PortService).all()}
    cost_by_key = dict(
        db.query(UsageStat.api_key_id, func.coalesce(func.sum(UsageStat.cost), 0.0))
        .filter(UsageStat.api_key_id.isnot(None))
        .group_by(UsageStat.api_key_id).all()
    )
    out = []
    for k in db.query(ApiKey).order_by(ApiKey.id.desc()).all():
        out.append({
            "id": k.id, "name": k.name, "port_id": k.port_id,
            "port_name": port_names.get(k.port_id, "—") if k.port_id else None,
            "quota_tokens": k.quota_tokens, "used_tokens": k.used_tokens,
            "enabled": k.enabled, "cost": round(float(cost_by_key.get(k.id, 0.0)), 4),
        })
    return out


@router.get("/keys/{key_id}/daily")
def key_daily(key_id: int, rng: str = "30d", db: Session = Depends(get_db),
              _: object = Depends(get_current_user)):
    """Daily usage/cost trend for a single API key."""
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(404, "Key not found")
    since = _since_day(rng)
    q = db.query(
        UsageStat.day,
        func.coalesce(func.sum(UsageStat.requests), 0),
        func.coalesce(func.sum(UsageStat.prompt_tokens), 0),
        func.coalesce(func.sum(UsageStat.completion_tokens), 0),
        func.coalesce(func.sum(UsageStat.cost), 0.0),
    ).filter(UsageStat.api_key_id == key_id)
    if since:
        q = q.filter(UsageStat.day >= since)
    daily = []
    for row in q.group_by(UsageStat.day).order_by(UsageStat.day).all():
        daily.append({
            "day": row[0], "requests": int(row[1]),
            "total_tokens": int(row[2]) + int(row[3]), "cost": round(float(row[4]), 4),
        })
    pricing = get_pricing(db)
    return {"key_id": key_id, "name": key.name, "range": rng,
            "currency": pricing.get("currency", "USD"), "daily": daily}


def _csv_response(rows: list[list], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(r)
    # BOM so Excel reads UTF-8 (model/port names may be CJK) correctly.
    data = "﻿" + buf.getvalue()
    return Response(
        content=data, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export.csv")
def export_csv(rng: str = "30d", scope: str = "summary", db: Session = Depends(get_db),
               _: object = Depends(get_current_user)):
    """Export usage as CSV. scope=summary -> per day/port/model port-level rows;
    scope=keys -> per API key cumulative usage."""
    since = _since_day(rng)
    port_names = {p.id: p.name for p in db.query(PortService).all()}

    if scope == "keys":
        cost_by_key = dict(
            db.query(UsageStat.api_key_id, func.coalesce(func.sum(UsageStat.cost), 0.0))
            .filter(UsageStat.api_key_id.isnot(None))
            .group_by(UsageStat.api_key_id).all()
        )
        rows: list[list] = [["key_id", "name", "port", "used_tokens", "quota_tokens",
                             "enabled", "cost"]]
        for k in db.query(ApiKey).order_by(ApiKey.id.desc()).all():
            rows.append([
                k.id, k.name, port_names.get(k.port_id, "") if k.port_id else "all",
                k.used_tokens, k.quota_tokens, int(k.enabled),
                round(float(cost_by_key.get(k.id, 0.0)), 6),
            ])
        return _csv_response(rows, "usage_keys.csv")

    # default: summary (port-level rows, api_key_id IS NULL)
    q = db.query(
        UsageStat.day, UsageStat.port_id, UsageStat.model,
        func.coalesce(func.sum(UsageStat.requests), 0),
        func.coalesce(func.sum(UsageStat.errors), 0),
        func.coalesce(func.sum(UsageStat.prompt_tokens), 0),
        func.coalesce(func.sum(UsageStat.completion_tokens), 0),
        func.coalesce(func.sum(UsageStat.cost), 0.0),
    ).filter(UsageStat.api_key_id.is_(None))
    if since:
        q = q.filter(UsageStat.day >= since)
    q = q.group_by(UsageStat.day, UsageStat.port_id, UsageStat.model).order_by(UsageStat.day)
    rows = [["day", "port", "model", "requests", "errors", "prompt_tokens",
             "completion_tokens", "total_tokens", "cost"]]
    for r in q.all():
        rows.append([
            r[0], port_names.get(r[1], f"#{r[1]}" if r[1] else "—"), r[2] or "—",
            int(r[3]), int(r[4]), int(r[5]), int(r[6]), int(r[5]) + int(r[6]),
            round(float(r[7]), 6),
        ])
    return _csv_response(rows, f"usage_{rng}.csv")


class PricingIn(BaseModel):
    currency: str = "USD"
    default: dict = {"in": 0.0, "out": 0.0}
    models: dict = {}


@router.get("/pricing")
def read_pricing(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    return get_pricing(db, fresh=True)


@router.put("/pricing")
def write_pricing(body: PricingIn, db: Session = Depends(get_db),
                  _: object = Depends(require_admin)):
    return set_pricing(db, body.model_dump())
