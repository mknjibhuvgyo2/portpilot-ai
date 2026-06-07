"""User management / RBAC admin API (admin-only).

Admins can list/create/update/delete users and assign the admin|user role.
Guards prevent locking yourself out: you can't delete your own account, and you
can't delete/demote/deactivate the last remaining active admin.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.core.security import hash_password
from app.db.models import User, UserRole
from app.db.session import get_db

router = APIRouter(prefix="/api/users", tags=["users"])


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


def _serialize(u: User) -> dict:
    return {"id": u.id, "username": u.username,
            "role": u.role.value if isinstance(u.role, UserRole) else u.role,
            "is_active": u.is_active, "created_at": u.created_at}


def _active_admin_count(db: Session, exclude_id: int | None = None) -> int:
    q = db.query(User).filter(User.role == UserRole.admin, User.is_active.is_(True))
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return q.count()


@router.get("")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [_serialize(u) for u in db.query(User).order_by(User.id).all()]


@router.post("")
def create_user(body: UserCreate, db: Session = Depends(get_db),
                _: User = Depends(require_admin)):
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(400, "username and password are required")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(409, "username already exists")
    if body.role not in ("admin", "user"):
        raise HTTPException(400, "invalid role")
    u = User(username=username, password_hash=hash_password(body.password),
             role=UserRole(body.role), is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return _serialize(u)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "not found")
    # guard: don't strip the last active admin of its admin powers
    losing_admin = u.role == UserRole.admin and u.is_active and (
        (body.role is not None and body.role != "admin")
        or body.is_active is False
    )
    if losing_admin and _active_admin_count(db, exclude_id=u.id) == 0:
        raise HTTPException(400, "cannot demote/deactivate the last active admin")

    if body.role is not None:
        if body.role not in ("admin", "user"):
            raise HTTPException(400, "invalid role")
        u.role = UserRole(body.role)
    if body.is_active is not None:
        u.is_active = bool(body.is_active)
    if body.password:
        u.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(u)
    return _serialize(u)


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "not found")
    if u.id == admin.id:
        raise HTTPException(400, "cannot delete your own account")
    if u.role == UserRole.admin and u.is_active and _active_admin_count(db, exclude_id=u.id) == 0:
        raise HTTPException(400, "cannot delete the last active admin")
    db.delete(u)
    db.commit()
    return {"ok": True}
