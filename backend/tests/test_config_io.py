"""Offline tests for config export/import (backup & migration)."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi.testclient import TestClient  # noqa: E402


def _client():
    from app.auth.deps import require_admin
    from app.db.models import Base, ModelAlias, PortService, Provider
    from app.db.session import SessionLocal, engine
    from app.main import app

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        db.query(PortService).delete()
        db.query(ModelAlias).delete()
        db.query(Provider).delete()
        p = Provider(name="P1", kind="ollama", base_url="http://127.0.0.1:11434",
                     api_key="secret-key", gpu_index="0", weight=2)
        db.add(p)
        db.flush()
        db.add(ModelAlias(alias="A1", targets=[{"provider_id": p.id, "model": "qwen", "lb_group": "g"}],
                          fallback_text="sorry", params={"lb_strategy": "least_vram"}))
        db.add(PortService(name="Svc", slug="svc", port=9001, model_alias="A1",
                           system_prompt="be nice"))
        db.commit()
    finally:
        db.close()
    app.dependency_overrides[require_admin] = lambda: object()
    return TestClient(app)


def test_export_shape_and_secret_redaction():
    c = _client()
    d = c.get("/api/config/export").json()
    assert d["version"] == 1
    assert d["providers"][0]["name"] == "P1"
    assert d["providers"][0]["has_key"] is True
    assert "api_key" not in d["providers"][0]            # redacted by default
    # alias targets reference provider by NAME, not internal id
    assert d["aliases"][0]["targets"][0]["provider_name"] == "P1"
    assert d["aliases"][0]["params"]["lb_strategy"] == "least_vram"
    assert d["ports"][0]["slug"] == "svc"


def test_export_with_secrets():
    c = _client()
    d = c.get("/api/config/export", params={"include_secrets": True}).json()
    assert d["providers"][0]["api_key"] == "secret-key"


def test_import_roundtrip_remaps_provider_ids():
    from app.db.models import ModelAlias, PortService, Provider
    from app.db.session import SessionLocal

    c = _client()
    exported = c.get("/api/config/export").json()

    # wipe everything, then import the exported config
    db = SessionLocal()
    db.query(PortService).delete(); db.query(ModelAlias).delete(); db.query(Provider).delete()
    db.commit(); db.close()

    r = c.post("/api/config/import", json={"data": exported}).json()
    assert r["added"] == {"providers": 1, "aliases": 1, "ports": 1}

    db = SessionLocal()
    try:
        prov = db.query(Provider).filter(Provider.name == "P1").one()
        alias = db.query(ModelAlias).filter(ModelAlias.alias == "A1").one()
        port = db.query(PortService).filter(PortService.slug == "svc").one()
        # provider_id remapped to the NEW provider id
        assert alias.targets[0]["provider_id"] == prov.id
        assert alias.targets[0]["model"] == "qwen"
        assert alias.targets[0]["lb_group"] == "g"
        assert port.model_alias == "A1"
        assert port.system_prompt == "be nice"
    finally:
        db.close()


def test_import_merge_skips_existing():
    c = _client()  # seeds P1/A1/svc
    exported = c.get("/api/config/export").json()
    # import the same config without wiping -> everything already exists -> skipped
    r = c.post("/api/config/import", json={"data": exported}).json()
    assert r["added"] == {"providers": 0, "aliases": 0, "ports": 0}
    assert r["skipped"]["providers"] == 1
    assert r["skipped"]["ports"] == 1
