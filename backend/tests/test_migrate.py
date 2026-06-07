"""Tests for the lightweight auto-migration (additive column adds)."""
import os
import tempfile

from sqlalchemy import Boolean, Integer, String, create_engine, inspect, text

from app.db.migrate import _literal, _type_default, auto_migrate


def test_literal():
    assert _literal(True) == "1"
    assert _literal(False) == "0"
    assert _literal(5) == "5"
    assert _literal(None) == "NULL"
    assert _literal("a'b") == "'a''b'"


def test_type_default():
    assert _type_default(Integer()) == 0
    assert _type_default(Boolean()) is False
    assert _type_default(String()) == ""


def _tmp_engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return create_engine(f"sqlite:///{tmp.name}"), tmp.name


def test_auto_migrate_adds_missing_columns():
    eng, path = _tmp_engine()
    try:
        # an OLD providers table missing gpu_index / weight / extra
        with eng.begin() as c:
            c.execute(text(
                'CREATE TABLE providers (id INTEGER PRIMARY KEY, name VARCHAR, '
                'kind VARCHAR, base_url VARCHAR, api_key VARCHAR, enabled BOOLEAN, '
                'created_at DATETIME)'))
        added = auto_migrate(eng)
        cols = {col["name"] for col in inspect(eng).get_columns("providers")}
        assert {"gpu_index", "weight", "extra"} <= cols
        assert "providers.weight" in added
        # other model tables didn't exist -> not touched by ADD COLUMN
        assert all(a.startswith("providers.") for a in added)
    finally:
        eng.dispose()
        os.unlink(path)


def test_auto_migrate_idempotent_on_fresh_schema():
    from app.db.models import Base
    eng, path = _tmp_engine()
    try:
        Base.metadata.create_all(eng)
        assert auto_migrate(eng) == []      # already in sync
        assert auto_migrate(eng) == []      # and again
    finally:
        eng.dispose()
        os.unlink(path)
