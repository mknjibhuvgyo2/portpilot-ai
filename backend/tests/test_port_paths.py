"""Tests for custom port paths (path_alias) + editable gateway slug."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from app.apps.base import PortConfig  # noqa: E402


def test_path_alias_mounts_extra_route_generic_chat():
    from app.apps.generic_chat import build_generic_chat_app
    cfg = PortConfig(id=1, name="x", slug="x", port=9001, app_type="generic_chat",
                     model_alias="a", path_alias="/myapi")
    app = build_generic_chat_app(cfg)
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/myapi" in paths and "/v1/chat/completions" in paths


def test_no_path_alias_means_no_extra_route():
    from app.apps.generic_chat import build_generic_chat_app
    cfg = PortConfig(id=1, name="x", slug="x", port=9002, app_type="generic_chat", model_alias="a")
    app = build_generic_chat_app(cfg)
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/v1/chat/completions" in paths
    assert not any(p == "/myapi" for p in paths)


def test_path_alias_mounts_on_embedding_and_rerank():
    from app.apps.embedding import build_embedding_app
    from app.apps.rerank import build_rerank_app
    e = build_embedding_app(PortConfig(id=1, name="e", slug="e", port=9003,
                                       app_type="embedding", model_alias="m", path_alias="/vec"))
    r = build_rerank_app(PortConfig(id=2, name="r", slug="r", port=9004,
                                    app_type="rerank", model_alias="m", path_alias="/rank"))
    assert "/vec" in {x.path for x in e.routes if hasattr(x, "path")}
    assert "/rank" in {x.path for x in r.routes if hasattr(x, "path")}


def _admin_client():
    from fastapi.testclient import TestClient

    from app.auth.deps import require_admin
    from app.db.models import Base, PortService
    from app.db.session import SessionLocal, engine
    from app.main import app

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        db.query(PortService).filter(PortService.slug.in_(["pp1", "pp2", "pp-x"])).delete()
        a = PortService(name="A", slug="pp1", port=9501)
        b = PortService(name="B", slug="pp2", port=9502)
        db.add(a); db.add(b); db.commit()
        ida = a.id
    finally:
        db.close()
    app.dependency_overrides[require_admin] = lambda: object()
    return TestClient(app), ida


def test_patch_sets_path_alias_and_slug():
    c, pid = _admin_client()
    try:
        r = c.patch(f"/api/ports/{pid}", json={"path_alias": "myapi", "slug": "pp-x"})
        assert r.status_code == 200
        d = r.json()
        assert d["path_alias"] == "/myapi"          # leading slash auto-added
        assert d["slug"] == "pp-x"
        assert d["restart_needed"] is False          # port isn't running
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_patch_duplicate_slug_rejected():
    c, pid = _admin_client()
    try:
        # pp2 already exists -> renaming pp1 to pp2 must 409
        r = c.patch(f"/api/ports/{pid}", json={"slug": "pp2"})
        assert r.status_code == 409
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_patch_reserved_path_rejected():
    c, pid = _admin_client()
    try:
        r = c.patch(f"/api/ports/{pid}", json={"path_alias": "/health"})
        assert r.status_code == 422   # validator rejects reserved endpoint
    finally:
        from app.main import app
        app.dependency_overrides.clear()
