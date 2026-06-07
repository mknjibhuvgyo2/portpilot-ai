"""Generate Nginx / Caddy reverse-proxy configs for the registered ports.

Two routing modes:
  - gateway: public traffic goes through the hub gateway (`/gw/<slug>/`) on the
    hub's own port, so API-key enforcement and usage accounting still apply.
    Recommended for intranet exposure.
  - direct: public traffic hits each port's uvicorn directly, bypassing the
    gateway (no key check, no usage accounting). Lower latency, fewer features.

Two layouts:
  - path  (no domain): one virtual host, each service under `/<slug>/`.
  - host  (domain set): one virtual host per service at `<slug>.<domain>`.

The generators are pure functions of a plain list of port dicts, so they are
trivially unit-testable and never touch the DB or network.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

Kind = str   # "nginx" | "caddy"
Mode = str   # "gateway" | "direct"


def _label(name: str) -> str:
    """Collapse whitespace so a service name is safe inside a single-line
    config comment (names can't normally contain newlines, but be defensive)."""
    return re.sub(r"\s+", " ", (name or "").strip()) or "service"


def _header(kind: str, mode: str, layout: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    note = ("routes through the hub API gateway — keeps API-key enforcement + usage accounting"
            if mode == "gateway"
            else "routes directly to each port — bypasses the gateway (no key check / no usage stats)")
    return (f"# AI Port Hub — generated {kind} reverse-proxy config\n"
            f"# mode={mode} ({note})\n"
            f"# layout={layout} · generated {ts}\n"
            f"# Edit listen address / TLS as needed, then reload your proxy.\n")


def _target(port: dict, mode: str, hub_host: str, hub_port: int) -> tuple[str, str]:
    """Return (upstream_host_port, path_prefix) for proxying one service."""
    if mode == "gateway":
        return f"{hub_host}:{hub_port}", f"/gw/{port['slug']}/"
    return f"{hub_host}:{port['port']}", "/"


# --------------------------------------------------------------------------- #
# Nginx
# --------------------------------------------------------------------------- #

_NGINX_PROXY_DIRECTIVES = """\
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;            # stream SSE token-by-token
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        client_max_body_size 25m;       # allow base64 image uploads"""


def _nginx(ports: list[dict], mode: str, domain: str, hub_host: str, hub_port: int) -> str:
    layout = "host" if domain else "path"
    out = [_header("nginx", mode, layout), ""]

    if domain:  # one server per service at <slug>.<domain>
        for p in ports:
            up, prefix = _target(p, mode, hub_host, hub_port)
            out.append(f"# {_label(p['name'])}" + ("  [API key required]" if p.get("auth_required") else ""))
            out.append("server {")
            out.append("    listen 80;")
            out.append(f"    server_name {p['slug']}.{domain};")
            out.append("    location / {")
            out.append(f"        proxy_pass http://{up}{prefix};")
            out.append(_NGINX_PROXY_DIRECTIVES)
            out.append("    }")
            out.append("}")
            out.append("")
    else:  # single server, each service under /<slug>/
        out.append("server {")
        out.append("    listen 80;")
        out.append("    server_name _;")
        out.append("")
        for p in ports:
            up, prefix = _target(p, mode, hub_host, hub_port)
            tag = "  [API key required]" if p.get("auth_required") else ""
            out.append(f"    # {_label(p['name'])}{tag}")
            out.append(f"    location /{p['slug']}/ {{")
            out.append(f"        proxy_pass http://{up}{prefix};")
            out.append(_NGINX_PROXY_DIRECTIVES)
            out.append("    }")
            out.append("")
        out.append("}")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Caddy
# --------------------------------------------------------------------------- #

def _caddy_reverse(up: str, rewrite_to: str | None) -> list[str]:
    lines = [f"    reverse_proxy {up} {{"]
    lines.append("        flush_interval -1")  # stream SSE immediately
    lines.append("    }")
    if rewrite_to is not None:
        return [f"    rewrite * {rewrite_to}", *lines]
    return lines


def _caddy(ports: list[dict], mode: str, domain: str, hub_host: str, hub_port: int,
           tls: bool) -> str:
    layout = "host" if domain else "path"
    out = [_header("caddy", mode, layout)]
    if not tls:
        out.append("# auto_https off / http:// addresses keep Caddy on plain HTTP\n")

    if domain:  # one site per service at <slug>.<domain>
        for p in ports:
            up, prefix = _target(p, mode, hub_host, hub_port)
            scheme = "" if tls else "http://"
            addr = f"{scheme}{p['slug']}.{domain}"
            rewrite = f"/gw/{p['slug']}{{uri}}" if mode == "gateway" else None
            tag = "  # API key required" if p.get("auth_required") else ""
            out.append(f"{addr} {{{tag}")
            out.extend(_caddy_reverse(up, rewrite))
            out.append("}")
            out.append("")
    else:  # single site, each service under /<slug>/*
        addr = ":80" if tls else "http://:80"
        out.append(f"{addr} {{")
        for p in ports:
            up, _ = _target(p, mode, hub_host, hub_port)
            # handle_path strips the /<slug> prefix; re-add the gateway path.
            rewrite = f"/gw/{p['slug']}{{uri}}" if mode == "gateway" else None
            tag = "  # API key required" if p.get("auth_required") else ""
            out.append(f"    handle_path /{p['slug']}/* {{{tag}")
            if rewrite is not None:
                out.append(f"        rewrite * /gw/{p['slug']}{{uri}}")
            out.append(f"        reverse_proxy {up} {{")
            out.append("            flush_interval -1")
            out.append("        }")
            out.append("    }")
            out.append("")
        out.append("}")
        out.append("")
    return "\n".join(out)


def generate(kind: str, ports: list[dict], *, mode: str = "gateway", domain: str = "",
             hub_host: str = "127.0.0.1", hub_port: int = 8000, tls: bool = True) -> str:
    """Render a reverse-proxy config. `kind` is 'nginx' or 'caddy'."""
    domain = (domain or "").strip().lstrip(".")
    hub_host = (hub_host or "127.0.0.1").strip()
    mode = mode if mode in ("gateway", "direct") else "gateway"
    if kind == "caddy":
        return _caddy(ports, mode, domain, hub_host, hub_port, tls)
    return _nginx(ports, mode, domain, hub_host, hub_port)


def filename_for(kind: str, domain: str = "") -> str:
    return "Caddyfile" if kind == "caddy" else "porthub.conf"
