"""Tests for live model/config hot-swap on running ports (no restart)."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from app.apps.base import PortConfig  # noqa: E402
from app.ports.manager import HOT_SWAP_FIELDS, PortManager  # noqa: E402


class _FakeRunner:
    def __init__(self, config: PortConfig, alive: bool = True):
        self.config = config
        self._alive = alive

    @property
    def alive(self) -> bool:
        return self._alive


def _cfg(**kw) -> PortConfig:
    base = dict(id=1, name="p", slug="p", port=9001, app_type="generic_chat", model_alias="a")
    base.update(kw)
    return PortConfig(**base)


def test_update_config_hotswaps_live_fields_only():
    m = PortManager()
    cfg = _cfg(model_alias="old", system_prompt="hi", concurrency=4)
    m._runners[1] = _FakeRunner(cfg)
    ok = m.update_config(1, {
        "model_alias": "new", "system_prompt": "bye", "concurrency": 16,
        "timeout": 9.0, "port": 9999, "auth_required": True,
    })
    assert ok is True
    assert cfg.model_alias == "new"
    assert cfg.system_prompt == "bye"
    assert cfg.concurrency == 16
    assert cfg.timeout == 9.0
    # immutable / non-subprocess fields are not touched on the live config
    assert cfg.port == 9001
    assert cfg.auth_required is False


def test_update_config_returns_false_when_not_running():
    m = PortManager()
    assert m.update_config(123, {"model_alias": "x"}) is False


def test_update_config_returns_false_for_dead_runner():
    m = PortManager()
    m._runners[1] = _FakeRunner(_cfg(), alive=False)
    assert m.update_config(1, {"model_alias": "x"}) is False


def test_hot_swap_fields_set():
    # process-bound identity must never hot-swap
    for f in ("id", "port", "slug", "app_type"):
        assert f not in HOT_SWAP_FIELDS
    # per-request runtime knobs must hot-swap
    for f in ("model_alias", "system_prompt", "concurrency", "streaming",
              "timeout", "max_retries", "logging_enabled", "log_keep"):
        assert f in HOT_SWAP_FIELDS


def test_patch_endpoint_returns_hot_swapped_flag():
    from fastapi.testclient import TestClient

    from app.auth.deps import get_current_user
    from app.db.models import Base, PortService, User, UserRole
    from app.db.session import SessionLocal, engine
    from app.main import app

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        db.query(PortService).filter_by(slug="hsport").delete()
        p = PortService(name="t", slug="hsport", port=9555)
        db.add(p)
        db.commit()
        pid = p.id
    finally:
        db.close()

    admin = User(username="hsadmin", password_hash="x", role=UserRole.admin, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: admin
    try:
        client = TestClient(app)
        r = client.patch(f"/api/ports/{pid}", json={"model_alias": "newalias"})
        assert r.status_code == 200
        data = r.json()
        assert data["model_alias"] == "newalias"
        # port is not running, so nothing to hot-swap
        assert data["hot_swapped"] is False
    finally:
        app.dependency_overrides.clear()
