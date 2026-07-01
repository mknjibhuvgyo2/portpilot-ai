"""Auto-unload local models: default on; LM Studio gets a `ttl` injected into the
request body; Ollama's native base is derived (strip /v1); the toggle honors the
`auto_unload` Setting."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from app.db.models import Base, Setting  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models_layer import unload  # noqa: E402

Base.metadata.create_all(engine)


def _set(enabled, ttl=60):
    db = SessionLocal()
    try:
        db.query(Setting).filter(Setting.key == "auto_unload").delete()
        db.add(Setting(key="auto_unload", value={"enabled": enabled, "lms_ttl": ttl}))
        db.commit()
    finally:
        db.close()
    unload.invalidate()


def test_default_enabled():
    db = SessionLocal()
    try:
        db.query(Setting).filter(Setting.key == "auto_unload").delete()
        db.commit()
    finally:
        db.close()
    unload.invalidate()
    assert unload.enabled() is True   # default on even with no row


def test_inject_ttl_only_for_lmstudio():
    _set(True, 45)
    b = {}
    unload.inject_request_unload(b, "lmstudio")
    assert b.get("ttl") == 45
    for k in ("ollama", "openai_compat", "llamacpp", ""):
        b2 = {}
        unload.inject_request_unload(b2, k)
        assert "ttl" not in b2


def test_disabled_injects_nothing():
    _set(False)
    assert unload.enabled() is False
    b = {}
    unload.inject_request_unload(b, "lmstudio")
    assert b == {}
    _set(True)  # restore for other tests


def test_ollama_native_base_strips_v1():
    assert unload._ollama_native_base("http://h:11434/v1") == "http://h:11434"
    assert unload._ollama_native_base("http://h:11434/") == "http://h:11434"
