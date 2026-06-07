"""Dashboard / monitoring API: the front page data."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.models import ModelAlias, PortService, Provider
from app.db.session import get_db
from app.monitor.gpu import gpu_stats
from app.monitor.metrics import metrics
from app.ports.manager import manager

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    ports = db.query(PortService).order_by(PortService.id).all()
    providers = {p.id: p for p in db.query(Provider).all()}
    aliases = {a.alias: a for a in db.query(ModelAlias).all()}

    def engines_for(alias_name: str) -> list[dict]:
        """Resolve a port's alias to its backing engines (provider/model/GPU)."""
        a = aliases.get(alias_name)
        if not a:
            return []
        out = []
        for i, t in enumerate(a.targets or []):
            prov = providers.get(t.get("provider_id"))
            if not prov:
                continue
            out.append({
                "provider": prov.name, "kind": prov.kind,
                "model": t.get("model", ""),
                "gpu_index": (prov.gpu_index or "").strip(),
                "primary": i == 0,
            })
        return out

    port_cards = []
    running = 0
    total_req = 0
    total_err = 0
    for p in ports:
        is_running = manager.is_running(p.id)
        if is_running:
            running += 1
        snap = metrics.snapshot(p.id)
        total_req += snap["total"]
        total_err += snap["errors"]
        engines = engines_for(p.model_alias)
        gpu_indices = sorted({e["gpu_index"] for e in engines if e["gpu_index"]})
        port_cards.append({
            "id": p.id, "name": p.name, "slug": p.slug, "port": p.port,
            "app_type": p.app_type, "model_alias": p.model_alias,
            "status": "running" if is_running else p.status.value,
            "metrics": snap, "engines": engines, "gpu_indices": gpu_indices,
        })

    # Reverse map: which running services sit on each physical GPU.
    gpu = gpu_stats()
    for g in gpu.get("gpus", []):
        idx = str(g["index"])
        g["services"] = [
            {"name": pc["name"], "id": pc["id"]}
            for pc in port_cards
            if pc["status"] == "running" and idx in pc["gpu_indices"]
        ]

    return {
        "summary": {
            "ports_total": len(ports),
            "ports_running": running,
            "requests_total": total_req,
            "errors_total": total_err,
        },
        "ports": port_cards,
        "gpu": gpu,
    }


@router.get("/gpu")
def gpu(_: object = Depends(get_current_user)):
    return gpu_stats()
