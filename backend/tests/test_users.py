"""Offline tests for user management / RBAC (admin guards)."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi.testclient import TestClient  # noqa: E402


def _client():
    from app.auth.deps import require_admin
    from app.core.security import hash_password
    from app.db.models import Base, User, UserRole
    from app.db.session import SessionLocal, engine
    from app.main import app

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        db.query(User).delete()
        admin = User(username="admin", password_hash=hash_password("x"), role=UserRole.admin)
        db.add(admin)
        db.commit()
        admin_id = admin.id
    finally:
        db.close()

    # act as the seeded admin
    class _Admin:
        id = admin_id
        role = UserRole.admin
    app.dependency_overrides[require_admin] = lambda: _Admin()
    return TestClient(app), admin_id


def test_create_list_and_roles():
    c, _ = _client()
    r = c.post("/api/users", json={"username": "bob", "password": "pw", "role": "user"})
    assert r.status_code == 200
    assert r.json()["role"] == "user"
    users = c.get("/api/users").json()
    names = {u["username"] for u in users}
    assert {"admin", "bob"} <= names

    # duplicate username rejected
    assert c.post("/api/users", json={"username": "bob", "password": "p"}).status_code == 409
    # invalid role rejected
    assert c.post("/api/users", json={"username": "x", "password": "p", "role": "root"}).status_code == 400


def test_promote_and_demote():
    c, _ = _client()
    bob = c.post("/api/users", json={"username": "bob", "password": "pw"}).json()
    r = c.patch(f"/api/users/{bob['id']}", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # now 2 admins -> can demote bob back
    assert c.patch(f"/api/users/{bob['id']}", json={"role": "user"}).status_code == 200


def test_cannot_remove_last_admin_or_self():
    c, admin_id = _client()
    # only one admin -> cannot demote it
    assert c.patch(f"/api/users/{admin_id}", json={"role": "user"}).status_code == 400
    # cannot deactivate the last admin
    assert c.patch(f"/api/users/{admin_id}", json={"is_active": False}).status_code == 400
    # cannot delete yourself
    assert c.delete(f"/api/users/{admin_id}").status_code == 400


def test_delete_user():
    c, _ = _client()
    bob = c.post("/api/users", json={"username": "bob", "password": "pw"}).json()
    assert c.delete(f"/api/users/{bob['id']}").status_code == 200
    names = {u["username"] for u in c.get("/api/users").json()}
    assert "bob" not in names
