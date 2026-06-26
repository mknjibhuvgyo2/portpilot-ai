"""Tests for the Import Wizard: JSON extraction from model output + the
auto-fill apply logic that turns a parsed spec into PORTHUB port services."""
import os
import tempfile

import pytest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi import HTTPException  # noqa: E402

from app.importer.router import _extract_json, _next_free_port, _slugify  # noqa: E402


def test_extract_json_plain():
    assert _extract_json('{"ports": [{"name": "x"}]}')["ports"][0]["name"] == "x"


def test_extract_json_strips_markdown_fence():
    assert _extract_json('```json\n{"ports": []}\n```') == {"ports": []}


def test_extract_json_finds_embedded_object():
    text = 'sure, here:\n{"ports": [1, 2]}\nhope that helps'
    assert _extract_json(text)["ports"] == [1, 2]


def test_extract_json_missing_ports_raises_502():
    with pytest.raises(HTTPException) as e:
        _extract_json('{"foo": 1}')
    assert e.value.status_code == 502


def test_extract_json_not_json_raises():
    with pytest.raises(HTTPException):
        _extract_json("totally not json at all")


def test_slugify():
    assert _slugify("My Cool Service!", 1) == "my-cool-service"
    assert _slugify("", 3) == "imported-3"
    assert _slugify("---", 2) == "imported-2"


def test_next_free_port_skips_used_and_8000():
    assert _next_free_port(set()) == 9001
    assert _next_free_port({9001, 9002}) == 9003
    assert _next_free_port(set(), start=8000) == 8001  # 8000 is reserved


def _admin_client():
    from fastapi.testclient import TestClient

    from app.auth.deps import require_admin
    from app.db.models import Base
    from app.db.session import engine
    from app.main import app

    Base.metadata.create_all(engine)
    app.dependency_overrides[require_admin] = lambda: object()
    return TestClient(app)


def test_apply_empty_ports_returns_400():
    c = _admin_client()
    try:
        assert c.post("/api/importer/apply", json={"ports": []}).status_code == 400
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_apply_autofills_slug_port_app_type_and_tasks():
    from app.db.models import PortService
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.query(PortService).filter(PortService.slug.like("imp-rev%")).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()

    c = _admin_client()
    try:
        spec = {"ports": [
            {"name": "Reviewer", "slug": "imp-rev", "app_type": "nonsense",
             "tasks": [{"name": "s1", "prompt": "be terse", "mode": "pool"}]},
            {"name": "Reviewer 2", "slug": "imp-rev"},  # slug collision -> imp-rev-2
        ]}
        r = c.post("/api/importer/apply", json=spec)
        assert r.status_code == 200
        created = r.json()["created"]
        assert len(created) == 2
        assert created[0]["slug"] == "imp-rev"
        assert created[1]["slug"] == "imp-rev-2"          # collision auto-resolved
        assert created[0]["app_type"] == "generic_chat"   # invalid type -> fallback
        ports = [x["port"] for x in created]
        assert len(set(ports)) == 2 and 8000 not in ports  # distinct, never 8000

        db = SessionLocal()
        try:
            p0 = db.get(PortService, created[0]["id"])
            tasks = (p0.extra or {}).get("tasks")
            assert tasks and tasks[0]["mode"] == "pool"
            assert tasks[0]["prompt"] == "be terse"
            p1 = db.get(PortService, created[1]["id"])
            # a port with no tasks gets a single default fixed task
            assert (p1.extra or {}).get("tasks")[0]["mode"] == "fixed"
        finally:
            db.close()
    finally:
        from app.main import app
        app.dependency_overrides.clear()
