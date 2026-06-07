"""Config backup / migration: export & import providers + aliases + ports.

Export references providers by **name** (alias targets store provider_name, not
the internal id) so a config can be imported into another instance with
different ids. API keys are excluded by default (set include_secrets=true to
include them). Import is a non-destructive merge: items whose name/alias/slug/
port already exist are skipped.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.db.models import ModelAlias, PortService, Provider
from app.db.session import get_db

router = APIRouter(prefix="/api/config", tags=["config"])

EXPORT_VERSION = 1


@router.get("/export")
def export_config(include_secrets: bool = False, db: Session = Depends(get_db),
                  _: object = Depends(require_admin)):
    providers = db.query(Provider).order_by(Provider.id).all()
    id_to_name = {p.id: p.name for p in providers}
    aliases = db.query(ModelAlias).order_by(ModelAlias.id).all()
    ports = db.query(PortService).order_by(PortService.id).all()

    def prov_dump(p: Provider) -> dict:
        d = {
            "name": p.name, "kind": p.kind, "base_url": p.base_url,
            "gpu_index": p.gpu_index, "weight": p.weight, "enabled": p.enabled,
            "extra": p.extra or {}, "has_key": bool(p.api_key),
        }
        if include_secrets:
            d["api_key"] = p.api_key
        return d

    def alias_dump(a: ModelAlias) -> dict:
        targets = []
        for t in (a.targets or []):
            name = id_to_name.get(t.get("provider_id"))
            if not name:
                continue
            targets.append({"provider_name": name, "model": t.get("model", ""),
                            "lb_group": t.get("lb_group", "")})
        return {"alias": a.alias, "targets": targets, "fallback_text": a.fallback_text,
                "params": a.params or {}, "enabled": a.enabled}

    def port_dump(p: PortService) -> dict:
        return {
            "name": p.name, "slug": p.slug, "port": p.port, "app_type": p.app_type,
            "model_alias": p.model_alias, "system_prompt": p.system_prompt,
            "streaming": p.streaming, "concurrency": p.concurrency, "timeout": p.timeout,
            "max_retries": p.max_retries, "logging_enabled": p.logging_enabled,
            "log_keep": p.log_keep, "auth_required": p.auth_required,
            "autostart": p.autostart, "extra": p.extra or {},
        }

    return {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "providers": [prov_dump(p) for p in providers],
        "aliases": [alias_dump(a) for a in aliases],
        "ports": [port_dump(p) for p in ports],
    }


class ImportBody(BaseModel):
    data: dict
    # only "merge" supported for now (skip existing by name/alias/slug/port)
    mode: str = "merge"


@router.post("/import")
def import_config(body: ImportBody, db: Session = Depends(get_db),
                  _: object = Depends(require_admin)):
    data = body.data or {}
    added = {"providers": 0, "aliases": 0, "ports": 0}
    skipped = {"providers": 0, "aliases": 0, "ports": 0}

    # ---- providers ----
    existing_prov = {p.name for p in db.query(Provider).all()}
    for pd in data.get("providers", []):
        name = (pd.get("name") or "").strip()
        if not name:
            continue
        if name in existing_prov:
            skipped["providers"] += 1
            continue
        db.add(Provider(
            name=name, kind=pd.get("kind", "openai_compat"), base_url=pd.get("base_url", ""),
            api_key=pd.get("api_key", ""), gpu_index=pd.get("gpu_index", ""),
            weight=int(pd.get("weight", 1) or 1), enabled=bool(pd.get("enabled", True)),
            extra=pd.get("extra") or {}))
        existing_prov.add(name)
        added["providers"] += 1
    db.flush()
    name_to_id = {p.name: p.id for p in db.query(Provider).all()}

    # ---- aliases ----
    existing_alias = {a.alias for a in db.query(ModelAlias).all()}
    for ad in data.get("aliases", []):
        al = (ad.get("alias") or "").strip()
        if not al:
            continue
        if al in existing_alias:
            skipped["aliases"] += 1
            continue
        targets = []
        for t in ad.get("targets", []):
            pid = name_to_id.get(t.get("provider_name"))
            if pid:
                targets.append({"provider_id": pid, "model": t.get("model", ""),
                                "lb_group": t.get("lb_group", "")})
        db.add(ModelAlias(alias=al, targets=targets, fallback_text=ad.get("fallback_text", ""),
                          params=ad.get("params") or {}, enabled=bool(ad.get("enabled", True))))
        existing_alias.add(al)
        added["aliases"] += 1

    # ---- ports ----
    existing_slug = {p.slug for p in db.query(PortService).all()}
    existing_port = {p.port for p in db.query(PortService).all()}
    for pd in data.get("ports", []):
        slug = (pd.get("slug") or "").strip()
        portnum = pd.get("port")
        if not slug or portnum is None:
            continue
        if slug in existing_slug or portnum in existing_port:
            skipped["ports"] += 1
            continue
        db.add(PortService(
            name=pd.get("name", slug), slug=slug, port=int(portnum),
            app_type=pd.get("app_type", "generic_chat"), model_alias=pd.get("model_alias", ""),
            system_prompt=pd.get("system_prompt", ""), streaming=bool(pd.get("streaming", True)),
            concurrency=int(pd.get("concurrency", 8) or 8), timeout=float(pd.get("timeout", 120) or 120),
            max_retries=int(pd.get("max_retries", 2) or 2),
            logging_enabled=bool(pd.get("logging_enabled", True)),
            log_keep=int(pd.get("log_keep", 10) or 10), auth_required=bool(pd.get("auth_required", False)),
            autostart=bool(pd.get("autostart", False)), extra=pd.get("extra") or {}))
        existing_slug.add(slug)
        existing_port.add(portnum)
        added["ports"] += 1

    db.commit()
    return {"added": added, "skipped": skipped}
