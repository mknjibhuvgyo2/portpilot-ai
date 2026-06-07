"""Offline tests for the usage-stats enhancements:
real-token capture, gateway usage sniffing, CSV export, per-key trend.
"""
import os
import tempfile

# Use an isolated temp DB before importing anything that binds the engine.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["HUB_DATABASE_URL"] = f"sqlite:///{_tmp.name.replace(os.sep, '/')}"

from fastapi.testclient import TestClient  # noqa: E402

from app.gateway.proxy import _extract_usage  # noqa: E402
from app.models_layer.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from app.models_layer.types import ChatMessage, ChatRequest  # noqa: E402


# ---------- gateway usage sniffing ----------

def test_extract_usage_from_plain_json():
    body = '{"choices":[{"message":{"content":"hi"}}],' \
           '"usage":{"prompt_tokens":12,"completion_tokens":7,"total_tokens":19}}'
    assert _extract_usage(body) == (12, 7)


def test_extract_usage_from_sse_usage_chunk():
    sse = (
        'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":30,"completion_tokens":5}}\n\n'
        'data: [DONE]\n\n'
    )
    assert _extract_usage(sse) == (30, 5)


def test_extract_usage_none_when_absent():
    assert _extract_usage('data: {"choices":[{"delta":{"content":"x"}}]}\n\n') is None


def test_extract_usage_takes_last_occurrence():
    # A trailing usage object should win over an earlier one.
    txt = '"prompt_tokens": 1, "completion_tokens": 1 ... "prompt_tokens": 99, "completion_tokens": 88'
    assert _extract_usage(txt) == (99, 88)


# ---------- streaming asks for real usage ----------

def test_stream_body_requests_include_usage():
    prov = OpenAICompatProvider(base_url="http://x", api_key="")
    req = ChatRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)
    body = prov._body("m", req, stream=True)
    assert body["stream_options"] == {"include_usage": True}
    # Non-streaming must NOT add stream_options.
    assert "stream_options" not in prov._body("m", req, stream=False)


# ---------- CSV export + per-key trend endpoints ----------

def _client_with_seed():
    from app.auth.deps import get_current_user
    from app.db.models import ApiKey, PortService, UsageStat
    from app.db.session import SessionLocal, engine
    from app.db.models import Base
    from app.main import app

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        db.query(UsageStat).delete()
        db.query(ApiKey).delete()
        db.query(PortService).delete()
        port = PortService(name="Scorer", slug="scorer", port=9001)
        db.add(port)
        db.flush()
        key = ApiKey(name="k1", key_prefix="abcd1234", key_hash="h",
                     port_id=port.id, quota_tokens=1000, used_tokens=120)
        db.add(key)
        db.flush()
        # port-level real-token row (api_key_id NULL) -> summary/export
        db.add(UsageStat(day="2026-06-03", port_id=port.id, api_key_id=None,
                         model="qwen2.5", requests=3, errors=0,
                         prompt_tokens=100, completion_tokens=40, cost=0.0021))
        # key-level row (api_key_id set) -> per-key trend
        db.add(UsageStat(day="2026-06-03", port_id=port.id, api_key_id=key.id,
                         model="qwen2.5", requests=2, errors=0,
                         prompt_tokens=60, completion_tokens=20, cost=0.0011))
        db.add(UsageStat(day="2026-06-04", port_id=port.id, api_key_id=key.id,
                         model="qwen2.5", requests=1, errors=0,
                         prompt_tokens=10, completion_tokens=5, cost=0.0003))
        db.commit()
        key_id = key.id
    finally:
        db.close()

    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app), key_id


def test_export_summary_csv():
    client, _ = _client_with_seed()
    r = client.get("/api/usage/export.csv", params={"rng": "all", "scope": "summary"})
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    text = r.text.lstrip("﻿")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("day,port,model,requests")
    # only the api_key_id NULL row is exported in summary scope
    body = "\n".join(lines[1:])
    assert "Scorer" in body and "qwen2.5" in body
    assert "2026-06-03" in body
    # the key-level rows (60/20, 10/5) must not appear in summary
    assert ",60," not in body


def test_export_keys_csv():
    client, _ = _client_with_seed()
    r = client.get("/api/usage/export.csv", params={"scope": "keys"})
    assert r.status_code == 200
    text = r.text.lstrip("﻿")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("key_id,name,port,used_tokens")
    assert "k1" in lines[1] and "120" in lines[1]


def test_key_daily_trend():
    client, key_id = _client_with_seed()
    r = client.get(f"/api/usage/keys/{key_id}/daily", params={"rng": "all"})
    assert r.status_code == 200
    data = r.json()
    assert data["key_id"] == key_id
    days = {d["day"]: d for d in data["daily"]}
    assert set(days) == {"2026-06-03", "2026-06-04"}
    assert days["2026-06-03"]["total_tokens"] == 80   # 60 + 20
    assert days["2026-06-04"]["total_tokens"] == 15   # 10 + 5


def test_key_daily_404_for_missing_key():
    client, _ = _client_with_seed()
    r = client.get("/api/usage/keys/999999/daily")
    assert r.status_code == 404
