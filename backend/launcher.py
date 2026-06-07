"""Standalone launcher for packaged builds (exe / .app / Linux binary).

Boots the FastAPI app with the bundled frontend, prints the local URL, and
opens the browser. Runtime data (SQLite, secret key, prompt library) is stored
in a `data/` folder next to the executable. Configure via HUB_* env vars or a
`.env` file next to the executable (see backend/.env.example).
"""
from __future__ import annotations

import os
import threading
import time
import webbrowser


def main() -> None:
    import uvicorn

    from app.core.config import settings
    from app.main import app

    host = os.environ.get("HUB_HOST", "127.0.0.1")
    port = int(os.environ.get("HUB_PORT", str(settings.port)))
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown_host}:{port}"

    if os.environ.get("HUB_OPEN_BROWSER", "1") != "0":
        def _open() -> None:
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    print("=" * 56)
    print("  PORTHUB / AI Port Hub")
    print(f"  Open:  {url}")
    print("  (first run prints the admin password below; Ctrl+C to stop)")
    print("=" * 56)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
