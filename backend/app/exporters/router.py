"""Reverse-proxy config export API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.models import PortService
from app.db.session import get_db
from app.exporters.reverse_proxy import filename_for, generate

router = APIRouter(prefix="/api/exporters", tags=["exporters"])


@router.get("/reverse-proxy")
def reverse_proxy(
    kind: str = Query("nginx", pattern="^(nginx|caddy)$"),
    mode: str = Query("gateway", pattern="^(gateway|direct)$"),
    domain: str = "",
    hub_host: str = "127.0.0.1",
    hub_port: int = 8000,
    tls: bool = True,
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    ports = [
        {"slug": p.slug, "port": p.port, "name": p.name, "auth_required": p.auth_required}
        for p in db.query(PortService).order_by(PortService.slug).all()
    ]
    content = generate(kind, ports, mode=mode, domain=domain,
                       hub_host=hub_host, hub_port=hub_port, tls=tls)
    return {
        "kind": kind, "mode": mode, "count": len(ports),
        "filename": filename_for(kind, domain), "content": content,
    }
