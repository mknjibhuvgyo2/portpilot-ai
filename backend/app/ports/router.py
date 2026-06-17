"""Port service management API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.apps.registry import list_templates
from app.auth.deps import get_current_user, require_admin
from app.db.models import PortService, PortStatus, RequestLog
from app.db.session import get_db
from app.monitor.metrics import metrics
from app.ports.manager import config_from_row, health_check, manager, port_in_use
from app.ports.schemas import PortCreate, PortOut, PortUpdate

router = APIRouter(prefix="/api/ports", tags=["ports"])


def _serialize(p: PortService) -> dict:
    out = PortOut.model_validate(p).model_dump()
    running = manager.is_running(p.id)
    # a stopped port whose host port is taken by another process is a conflict
    busy = (not running) and port_in_use(p.port)
    out["status"] = "running" if running else ("conflict" if busy else p.status.value)
    out["port_busy"] = busy
    out["path_alias"] = str((p.extra or {}).get("path_alias", "") or "")
    out["metrics"] = metrics.snapshot(p.id)
    return out


@router.get("/templates")
def templates(_: object = Depends(get_current_user)):
    return list_templates()


@router.get("")
def list_ports(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    return [_serialize(p) for p in db.query(PortService).order_by(PortService.id).all()]


def _apply_tasks(data: dict) -> dict:
    """Normalize the task flow: stash the full list in extra['tasks'] and keep
    model_alias/system_prompt in sync with tasks[0] (back-compat for the gateway
    and single-stage templates). Returns kwargs safe for the PortService model."""
    tasks = data.pop("tasks", None)
    debug = data.pop("debug", None)
    if tasks:
        first = tasks[0]
        if first.get("alias"):
            data["model_alias"] = first["alias"]
        if first.get("prompt"):
            data["system_prompt"] = first["prompt"]
        extra = dict(data.get("extra") or {})
        extra["tasks"] = tasks
        data["extra"] = extra
    if debug is not None:
        extra = dict(data.get("extra") or {})
        extra["debug"] = bool(debug)
        data["extra"] = extra
    return data


@router.post("", response_model=PortOut)
def create_port(body: PortCreate, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    if db.query(PortService).filter(PortService.slug == body.slug).first():
        raise HTTPException(409, "slug already exists")
    if db.query(PortService).filter(PortService.port == body.port).first():
        raise HTTPException(409, "port already in use")
    p = PortService(**_apply_tasks(body.model_dump()))
    db.add(p)
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.patch("/{port_id}")
def update_port(port_id: int, body: PortUpdate, db: Session = Depends(get_db),
                _: object = Depends(require_admin)):
    p = db.get(PortService, port_id)
    if not p:
        raise HTTPException(404, "not found")
    changed = body.model_dump(exclude_unset=True)

    # --- slug: gateway path segment; gateway is DB-driven so it's instant ---
    if "slug" in changed:
        new_slug = changed.pop("slug")
        if new_slug and new_slug != p.slug:
            if (db.query(PortService)
                    .filter(PortService.slug == new_slug, PortService.id != port_id).first()):
                raise HTTPException(409, "slug already exists")
            p.slug = new_slug

    # --- path_alias: extra URL path the subprocess serves; routes are bound at
    #     build time, so a change to a RUNNING port needs a restart to apply. ---
    restart_needed = False
    if "path_alias" in changed:
        pa = changed.pop("path_alias") or ""
        extra = dict(p.extra or {})
        if str(extra.get("path_alias", "") or "") != pa:
            extra["path_alias"] = pa
            p.extra = extra
            restart_needed = manager.is_running(port_id)

    if "tasks" in changed or "debug" in changed:
        changed.setdefault("extra", dict(p.extra or {}))
        changed = _apply_tasks(changed)
    for k, v in changed.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    # If the port is running, push the changes to the live subprocess so they
    # take effect immediately (model/prompt/runtime) — no restart needed.
    # auth_required/autostart are enforced outside the subprocess (gateway/
    # bootstrap) and already live via the DB, so any edit to a running port
    # applies without a restart.
    hot_swapped = bool(changed) and manager.update_config(port_id, changed)
    return {**_serialize(p), "hot_swapped": hot_swapped, "restart_needed": restart_needed}


@router.delete("/{port_id}")
def delete_port(port_id: int, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    p = db.get(PortService, port_id)
    if not p:
        raise HTTPException(404, "not found")
    manager.stop(port_id)
    db.query(RequestLog).filter(RequestLog.port_id == port_id).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/{port_id}/start", response_model=PortOut)
def start_port(port_id: int, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    p = db.get(PortService, port_id)
    if not p:
        raise HTTPException(404, "not found")
    if not manager.is_running(p.id) and port_in_use(p.port):
        raise HTTPException(409, f"端口 {p.port} 已被其他进程占用，无法启动 / port {p.port} already in use by another process")
    try:
        manager.start(config_from_row(p))
        p.status = PortStatus.running
        db.commit()
        db.refresh(p)
    except Exception as e:  # noqa: BLE001
        p.status = PortStatus.error
        db.commit()
        raise HTTPException(500, f"failed to start: {e}")
    return _serialize(p)


@router.post("/{port_id}/stop", response_model=PortOut)
def stop_port(port_id: int, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    p = db.get(PortService, port_id)
    if not p:
        raise HTTPException(404, "not found")
    manager.stop(port_id)
    p.status = PortStatus.stopped
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.get("/{port_id}/health")
async def port_health(port_id: int, db: Session = Depends(get_db),
                      _: object = Depends(get_current_user)):
    p = db.get(PortService, port_id)
    if not p:
        raise HTTPException(404, "not found")
    if not manager.is_running(port_id):
        return {"healthy": False, "running": False}
    healthy = await health_check(p.port)
    return {"healthy": healthy, "running": True}


@router.get("/{port_id}/logs")
def port_logs(port_id: int, db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = (db.query(RequestLog).filter(RequestLog.port_id == port_id)
            .order_by(RequestLog.id.desc()).limit(50).all())
    return [
        {
            "id": r.id, "ts": r.ts.isoformat(), "ok": r.ok, "latency_ms": r.latency_ms,
            "model_used": r.model_used, "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "request_excerpt": r.request_excerpt, "response_excerpt": r.response_excerpt,
            "error": r.error,
        }
        for r in rows
    ]
