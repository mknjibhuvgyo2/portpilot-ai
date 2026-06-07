"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import secrets
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _frozen() -> bool:
    """True when running from a PyInstaller bundle (exe/.app/binary)."""
    return bool(getattr(sys, "frozen", False))


if _frozen():
    # Bundled assets (read-only) live under the PyInstaller extraction dir;
    # writable runtime data lives next to the executable so it persists.
    _BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    _EXE_DIR = Path(sys.executable).resolve().parent
    DATA_DIR = _EXE_DIR / "data"
    _FRONTEND_DIST = _BUNDLE_DIR / "frontend" / "dist"
    _ENV_FILE = _EXE_DIR / ".env"
else:
    # Repository root: backend/app/core/config.py -> parents[3] == repo root
    REPO_ROOT = Path(__file__).resolve().parents[3]
    DATA_DIR = REPO_ROOT / "data"
    _FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
    _ENV_FILE = REPO_ROOT / "backend" / ".env"

DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_prefix="HUB_",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "AI Port Hub"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Security ---
    # If not provided, a stable secret is generated and persisted to data/secret.key
    secret_key: str = ""
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    algorithm: str = "HS256"

    # --- Admin bootstrap (first run only) ---
    admin_username: str = "admin"
    admin_password: str = ""  # if empty, a random one is generated and printed

    # --- Database ---
    database_url: str = f"sqlite:///{(DATA_DIR / 'hub.db').as_posix()}"

    # --- Frontend static dir (built Vue app) ---
    frontend_dist: str = str(_FRONTEND_DIST)

    # --- Defaults for new port services ---
    default_request_timeout: float = 120.0
    default_max_retries: int = 2
    default_concurrency: int = 8
    default_log_keep: int = 10  # keep last N request logs per port

    def resolved_secret_key(self) -> str:
        if self.secret_key:
            return self.secret_key
        key_file = DATA_DIR / "secret.key"
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
        key = secrets.token_urlsafe(48)
        key_file.write_text(key, encoding="utf-8")
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
