"""Tests for streaming Ollama model-pull progress (SSE)."""
import os
import tempfile
import uuid

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi.testclient import TestClient  # noqa: E402


def _setup(monkeypatch, lines, status=200):
    from app.auth.deps import require_admin
    from app.db.models import Base, Provider
    from app.db.session import SessionLocal, engine
    from app.main import app
    import app.models_layer.api as apimod

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        p = Provider(name=f"ol-pull-{uuid.uuid4().hex[:8]}", kind="ollama",
                     base_url=f"http://127.0.0.1:11434/{uuid.uuid4().hex[:6]}", weight=1)
        db.add(p)
        db.commit()
        pid = p.id
    finally:
        db.close()

    class _Stream:
        def __init__(self):
            self.status_code = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_lines(self):
            for ln in lines:
                yield ln

        async def aread(self):
            return b"boom"

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None):
            return _Stream()

    monkeypatch.setattr(apimod.httpx, "AsyncClient", _Client)
    app.dependency_overrides[require_admin] = lambda: object()
    return TestClient(app), pid


def test_pull_stream_emits_progress_and_done(monkeypatch):
    lines = [
        '{"status":"pulling manifest"}',
        '{"status":"downloading","total":100,"completed":50}',
        '{"status":"downloading","total":100,"completed":100}',
        '{"status":"success"}',
    ]
    c, pid = _setup(monkeypatch, lines)
    r = c.post(f"/api/models/providers/{pid}/pull-stream", json={"name": "qwen2.5:0.5b"})
    assert r.status_code == 200
    body = r.text
    assert '"percent": 50.0' in body
    assert '"percent": 100.0' in body
    assert '"done": true' in body          # final success marker appended


def test_pull_stream_reports_ollama_error(monkeypatch):
    c, pid = _setup(monkeypatch, ['{"error":"model not found"}'])
    r = c.post(f"/api/models/providers/{pid}/pull-stream", json={"name": "nope"})
    assert r.status_code == 200
    assert '"error": "model not found"' in r.text


def test_pull_stream_http_error_surfaced(monkeypatch):
    c, pid = _setup(monkeypatch, [], status=500)
    r = c.post(f"/api/models/providers/{pid}/pull-stream", json={"name": "x"})
    assert r.status_code == 200
    assert '"error"' in r.text and "ollama 500" in r.text


def test_pull_stream_requires_name(monkeypatch):
    c, pid = _setup(monkeypatch, [])
    assert c.post(f"/api/models/providers/{pid}/pull-stream", json={"name": "  "}).status_code == 400
