"""Tests for the port-conflict guard: detect a host port already taken by
another process, surface it as status='conflict', and refuse to start."""
import os
import socket
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")


def _listen():
    """Open a real listening socket on an OS-assigned free port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def test_port_in_use_true_while_listening_then_false_when_freed():
    from app.ports.manager import port_in_use

    s, port = _listen()
    try:
        assert port_in_use(port) is True
    finally:
        s.close()
    assert port_in_use(port) is False


def _admin_client():
    from fastapi.testclient import TestClient

    from app.auth.deps import get_current_user, require_admin
    from app.db.models import Base
    from app.db.session import engine
    from app.main import app

    Base.metadata.create_all(engine)
    app.dependency_overrides[require_admin] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app)


def test_busy_port_shows_conflict_and_blocks_start(monkeypatch):
    from app.db.models import Base, PortService, PortStatus
    from app.db.session import SessionLocal, engine
    from app.main import app
    from app.ports import router as ports_router

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        db.query(PortService).filter(PortService.slug == "conflict-demo").delete()
        p = PortService(name="C", slug="conflict-demo", port=9591,
                        status=PortStatus.stopped)
        db.add(p)
        db.commit()
        db.refresh(p)
        pid = p.id
    finally:
        db.close()

    # Simulate the host port being held by another process. The real socket
    # behavior of port_in_use() is covered by the test above; here we pin it so
    # the conflict status + start-refusal logic is exercised deterministically.
    monkeypatch.setattr(ports_router, "port_in_use", lambda port: port == 9591)

    c = _admin_client()
    try:
        row = next(r for r in c.get("/api/ports").json() if r["id"] == pid)
        assert row["status"] == "conflict"
        assert row["port_busy"] is True
        # the host port is taken -> start must be refused with 409
        assert c.post(f"/api/ports/{pid}/start").status_code == 409
    finally:
        app.dependency_overrides.clear()
