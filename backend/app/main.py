"""AI Port Hub - FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.apps.prompts_router import router as prompts_router
from app.auth.router import router as auth_router
from app.billing.router import router as keys_router
from app.billing.usage_router import router as usage_router
from app.chat.router import router as chat_router
from app.core.bootstrap import bootstrap
from app.core.config import settings
from app.db.models import PortService
from app.db.session import SessionLocal
from app.config_io.router import router as config_router
from app.exporters.router import router as exporters_router
from app.gateway.proxy import router as gateway_router
from app.importer.router import router as importer_router
from app.models_layer.api import router as models_router
from app.monitor.router import router as monitor_router
from app.ports.manager import config_from_row, manager
from app.ports.router import router as ports_router
from app.promptlab.router import router as promptlab_router
from app.tools.dedup import router as tools_router
from app.users.router import router as users_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hub")


def _autostart_ports() -> None:
    db = SessionLocal()
    try:
        for p in db.query(PortService).filter(PortService.autostart.is_(True)).all():
            try:
                manager.start(config_from_row(p))
                log.info("autostarted port service '%s' on :%d", p.slug, p.port)
            except Exception as e:  # noqa: BLE001
                log.error("failed to autostart '%s': %s", p.slug, e)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    _autostart_ports()
    yield
    manager.stop_all()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth_router, ports_router, models_router, monitor_router, chat_router,
          keys_router, usage_router, prompts_router, exporters_router,
          promptlab_router, config_router, users_router, tools_router, gateway_router,
          importer_router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


# --- Serve built frontend (single-process deployment) ---
_dist = Path(settings.frontend_dist)
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # API/gateway routes are handled above; everything else -> SPA index.
        if full_path.startswith(("api/", "gw/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        index = _dist / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"detail": "frontend not built"}, status_code=404)
else:
    @app.get("/")
    def root():
        return {"message": f"{settings.app_name} API. Frontend not built yet (run npm run build)."}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=int(os.getenv("HUB_PORT", settings.port)),
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
