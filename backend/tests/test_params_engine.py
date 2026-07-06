"""Template-param engine (app/apps/params.py): tpl_param reads extra.params;
apply_params_to_text substitutes [[param:key]] placeholders (incl. user-defined
custom params) into prompt text."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from app.apps.base import PortConfig  # noqa: E402
from app.apps.params import apply_params_to_text, tpl_param  # noqa: E402


def _cfg(params=None):
    return PortConfig(id=1, name="t", slug="s", port=9001, app_type="generic_chat",
                      model_alias="m", extra={"params": params} if params else {})


def test_tpl_param_default_and_override():
    assert tpl_param(_cfg(), "k", 12) == 12
    assert tpl_param(_cfg({"k": 5}), "k", 12) == 5
    assert tpl_param(_cfg({"k": ""}), "k", 12) == 12  # empty = default


def test_placeholder_substitution():
    cfg = _cfg({"brand": "Acme", "n": 3, "dims": {"a": 1}})
    assert apply_params_to_text(cfg, "for [[param:brand]], pick [[param:n]]") == "for Acme, pick 3"
    assert apply_params_to_text(cfg, "cfg=[[param:dims]]") == 'cfg={"a": 1}'
    assert apply_params_to_text(cfg, "keep [[param:missing]]") == "keep [[param:missing]]"
    assert apply_params_to_text(cfg, "plain") == "plain"
