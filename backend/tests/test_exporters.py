"""Tests for the reverse-proxy config exporter (pure generators + endpoint)."""
from app.exporters.reverse_proxy import filename_for, generate

PORTS = [
    {"slug": "scorer", "port": 9001, "name": "评分端口", "auth_required": True},
    {"slug": "vision", "port": 9002, "name": "Vision", "auth_required": False},
]


# ---------- nginx ----------

def test_nginx_gateway_path_layout():
    cfg = generate("nginx", PORTS, mode="gateway")
    assert "server_name _;" in cfg
    assert "location /scorer/ {" in cfg
    assert "location /vision/ {" in cfg
    # gateway mode proxies through the hub gateway path on the hub port
    assert "proxy_pass http://127.0.0.1:8000/gw/scorer/;" in cfg
    # streaming-friendly directives present
    assert "proxy_buffering off;" in cfg
    # auth-required service is annotated
    assert "[API key required]" in cfg


def test_nginx_direct_mode_targets_each_port():
    cfg = generate("nginx", PORTS, mode="direct")
    assert "proxy_pass http://127.0.0.1:9001/;" in cfg
    assert "proxy_pass http://127.0.0.1:9002/;" in cfg
    assert "/gw/" not in cfg


def test_nginx_host_layout_with_domain():
    cfg = generate("nginx", PORTS, mode="gateway", domain="ai.example.lan")
    assert "server_name scorer.ai.example.lan;" in cfg
    assert "server_name vision.ai.example.lan;" in cfg
    assert "location / {" in cfg


def test_nginx_custom_hub_host_port():
    cfg = generate("nginx", PORTS, mode="gateway", hub_host="10.0.0.5", hub_port=8080)
    assert "proxy_pass http://10.0.0.5:8080/gw/scorer/;" in cfg


# ---------- caddy ----------

def test_caddy_gateway_path_layout_rewrites_to_gateway():
    cfg = generate("caddy", PORTS, mode="gateway")
    assert ":80 {" in cfg
    assert "handle_path /scorer/* {" in cfg
    assert "rewrite * /gw/scorer{uri}" in cfg
    assert "reverse_proxy 127.0.0.1:8000" in cfg
    assert "flush_interval -1" in cfg


def test_caddy_host_layout_tls_off_uses_http_scheme():
    cfg = generate("caddy", PORTS, mode="gateway", domain="ai.example.lan", tls=False)
    assert "http://scorer.ai.example.lan {" in cfg
    assert "rewrite * /gw/scorer{uri}" in cfg


def test_caddy_direct_has_no_rewrite():
    cfg = generate("caddy", PORTS, mode="direct")
    assert "reverse_proxy 127.0.0.1:9001" in cfg
    assert "rewrite" not in cfg


def test_filename_for():
    assert filename_for("caddy") == "Caddyfile"
    assert filename_for("nginx") == "porthub.conf"


def test_empty_ports_still_valid():
    cfg = generate("nginx", [], mode="gateway")
    assert "server {" in cfg  # header + empty server block, no crash


def test_name_with_newline_stays_single_line_comment():
    # A name containing a newline must not break out of its config comment.
    ports = [{"slug": "x", "port": 9001, "name": "weird\nname", "auth_required": False}]
    cfg = generate("nginx", ports, mode="gateway")
    assert "# weird name" in cfg
    assert "weird\nname" not in cfg


# ---------- endpoint ----------

def test_reverse_proxy_endpoint():
    # Reuse the seeded TestClient harness from the usage tests.
    from tests.test_usage_enhancements import _client_with_seed
    client, _ = _client_with_seed()
    r = client.get("/api/exporters/reverse-proxy", params={"kind": "caddy", "mode": "gateway"})
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "caddy" and data["filename"] == "Caddyfile"
    assert "reverse_proxy" in data["content"]
    # invalid kind rejected by the pattern constraint
    assert client.get("/api/exporters/reverse-proxy", params={"kind": "apache"}).status_code == 422
