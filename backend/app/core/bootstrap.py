"""First-run initialization: create tables and bootstrap an admin user."""
from __future__ import annotations

import logging
import secrets

from app.core.config import settings
from app.core.security import hash_password
from app.db.migrate import auto_migrate
from app.db.models import Base, User, UserRole
from app.db.session import SessionLocal, engine

log = logging.getLogger("hub.bootstrap")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    auto_migrate(engine)  # add columns missing from older databases


def ensure_admin() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == settings.admin_username).first()
        if existing:
            return
        password = settings.admin_password or secrets.token_urlsafe(12)
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(password),
            role=UserRole.admin,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        banner = (
            "\n"
            "==================================================\n"
            "  AI Port Hub - admin account created\n"
            f"  username: {settings.admin_username}\n"
            f"  password: {password}\n"
        )
        if not settings.admin_password:
            banner += "  (random password - set HUB_ADMIN_PASSWORD to override)\n"
        banner += "==================================================\n"
        log.warning(banner)
        print(banner, flush=True)
    finally:
        db.close()


def bootstrap() -> None:
    init_db()
    ensure_admin()
