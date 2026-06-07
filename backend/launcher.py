"""Standalone launcher for packaged builds (exe / .app / Linux binary).

Boots the FastAPI app with the bundled frontend, prints the local URL, and
opens the browser. Runtime data (SQLite, secret key, prompt library) is stored
in a `data/` folder next to the executable. Configure via HUB_* env vars or a
`.env` file next to the executable (see backend/.env.example).

If the configured port is busy, the next free port is used automatically so a
double-clicked binary "just works" instead of failing to bind.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


def _port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pick_port(host: str, want: int, span: int = 20) -> int:
    if _port_free(host, want):
        return want
    for p in range(want + 1, want + span):
        if _port_free(host, p):
            return p
    return want  # nothing free in range; caller reports the error


def _pause_on_exit() -> None:
    """Keep the console window open when launched by double-click (frozen)."""
    if getattr(sys, "frozen", False) and os.name == "nt":
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass


def main() -> int:
    import uvicorn

    from app.core.config import settings
    from app.main import app

    host = os.environ.get("HUB_HOST", "127.0.0.1")
    want = int(os.environ.get("HUB_PORT", str(settings.port)))
    port = _pick_port(host, want)

    if not _port_free(host, port):
        print(f"\nERROR: no free port near {want} (is PORTHUB already running?).")
        print("Set HUB_PORT to a free port, or stop the other instance.")
        _pause_on_exit()
        return 1

    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown_host}:{port}"

    print("=" * 56)
    print("  PORTHUB / AI Port Hub")
    print(f"  Open:  {url}")
    if port != want:
        print(f"  (port {want} was busy - using {port} instead)")
    print("  (first run prints the admin password below; Ctrl+C to stop)")
    print("=" * 56)

    if os.environ.get("HUB_OPEN_BROWSER", "1") != "0":
        def _open() -> None:
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — surface startup errors before the window closes
        print(f"\nERROR: {e}")
        _pause_on_exit()
        raise SystemExit(1)
