"""Template-param helpers shared by ALL templates (generic + eval).

A port's 模板参数 live in ``config.extra["params"]``: keys declared by the
template's ``params_schema`` PLUS any number of user-defined custom keys added
in the UI. Two ways to consume them:

- ``tpl_param(config, key, default)`` — templates read declared knobs.
- ``apply_params_to_text(config, text)`` — ``[[param:<key>]]`` placeholders in
  any prompt text are replaced with the param value at request time, so custom
  params are usable from every stage prompt without code changes (unknown keys
  stay literal; dict/list values are JSON-encoded).

This is the extensibility contract: new knobs = declare in params_schema or just
add a custom key in the UI and reference it from a prompt.
"""
from __future__ import annotations

import json
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\[\[param:([^\]\s]+)\]\]")


def port_params(config) -> dict:
    p = (getattr(config, "extra", None) or {}).get("params")
    return p if isinstance(p, dict) else {}


def tpl_param(config, key: str, default: Any = None) -> Any:
    """Per-port override of a template behavior knob; empty/absent = default."""
    v = port_params(config).get(key)
    return v if v not in (None, "") else default


def apply_params_to_text(config, text: str) -> str:
    """Substitute ``[[param:key]]`` placeholders in prompt text."""
    if not text or "[[param:" not in text:
        return text
    p = port_params(config)

    def _rep(m: "re.Match[str]") -> str:
        v = p.get(m.group(1))
        if v in (None, ""):
            return m.group(0)
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    return _PLACEHOLDER.sub(_rep, text)
