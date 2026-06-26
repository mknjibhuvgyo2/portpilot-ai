"""Tests for task-flow normalization (_apply_tasks) that backs the Task Flows
editor, port create/update, and the Import Wizard: a tasks[] list is stashed in
extra['tasks'] while model_alias/system_prompt stay synced with tasks[0]."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from app.ports.router import _apply_tasks  # noqa: E402


def test_syncs_first_task_alias_and_prompt():
    data = _apply_tasks({"name": "x", "tasks": [
        {"name": "a", "alias": "fast", "prompt": "hi", "mode": "fixed", "pool": []},
        {"name": "b", "alias": "slow", "prompt": "bye", "mode": "fixed", "pool": []},
    ]})
    assert data["model_alias"] == "fast"
    assert data["system_prompt"] == "hi"
    assert data["extra"]["tasks"][1]["alias"] == "slow"
    assert "tasks" not in data  # popped off the top level into extra


def test_no_tasks_is_noop():
    data = _apply_tasks({"name": "x", "model_alias": "keep"})
    assert data == {"name": "x", "model_alias": "keep"}


def test_debug_flag_goes_to_extra():
    data = _apply_tasks({"name": "x", "debug": True})
    assert data["extra"]["debug"] is True
    assert "debug" not in data


def test_preserves_existing_extra():
    data = _apply_tasks({"name": "x", "extra": {"path_alias": "/p"},
                         "tasks": [{"name": "a", "alias": "", "prompt": "",
                                    "mode": "fixed", "pool": []}]})
    assert data["extra"]["path_alias"] == "/p"   # existing extra kept
    assert "tasks" in data["extra"]              # tasks added alongside


def test_first_task_empty_alias_leaves_model_alias_unset():
    data = _apply_tasks({"name": "x", "tasks": [
        {"name": "a", "alias": "", "prompt": "", "mode": "fixed", "pool": []},
    ]})
    assert "model_alias" not in data
