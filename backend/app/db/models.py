"""Database models for AI Port Hub."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.user)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Provider(Base):
    """A model backend endpoint: a vendor API or a local runtime (ollama/lmstudio/llama.cpp)."""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    # kind: openai_compat | ollama | lmstudio | llamacpp | anthropic | gemini ...
    kind: Mapped[str] = mapped_column(String(40), default="openai_compat")
    base_url: Mapped[str] = mapped_column(String(255))
    api_key: Mapped[str] = mapped_column(String(255), default="")
    # optional: pin to a GPU for local runtimes (e.g. "0" or "1") -- informational + LB
    gpu_index: Mapped[str] = mapped_column(String(16), default="")
    weight: Mapped[int] = mapped_column(Integer, default=1)  # load-balance weight
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelAlias(Base):
    """A friendly alias mapping to a primary model + ordered fallback chain."""

    __tablename__ = "model_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    # list of {provider_id, model} dicts, tried in order (first = primary)
    targets: Mapped[list] = mapped_column(JSON, default=list)
    # final guaranteed fallback text when every target fails (may be empty)
    fallback_text: Mapped[str] = mapped_column(Text, default="")
    # sampling defaults
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # temperature, top_p, max_tokens...
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PortStatus(str, enum.Enum):
    stopped = "stopped"
    running = "running"
    error = "error"


class PortService(Base):
    """A registered service bound to a host port, backed by an AppTemplate."""

    __tablename__ = "ports"
    __table_args__ = (UniqueConstraint("port", name="uq_port_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    port: Mapped[int] = mapped_column(Integer, index=True)
    app_type: Mapped[str] = mapped_column(String(80), default="generic_chat")
    model_alias: Mapped[str] = mapped_column(String(120), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")

    # runtime knobs
    streaming: Mapped[bool] = mapped_column(Boolean, default=True)
    concurrency: Mapped[int] = mapped_column(Integer, default=8)
    timeout: Mapped[float] = mapped_column(Float, default=120.0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    logging_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    log_keep: Mapped[int] = mapped_column(Integer, default=10)

    auth_required: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[PortStatus] = mapped_column(Enum(PortStatus), default=PortStatus.stopped)
    autostart: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiKey(Base):
    """API key for gateway access, optionally scoped to a project/port with a quota."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"), nullable=True)
    quota_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    used_tokens: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RequestLog(Base):
    """Per-port request log; pruned to keep only the last N per port."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    model_used: Mapped[str] = mapped_column(String(160), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    request_excerpt: Mapped[str] = mapped_column(Text, default="")
    response_excerpt: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class UsageStat(Base):
    """Cumulative usage aggregated per (day, port, api_key, model).

    Unlike RequestLog (pruned to last N per port), this is never pruned, so it
    backs the cost / quota statistics. Rows are upserted and accumulated.
    """

    __tablename__ = "usage_stats"
    __table_args__ = (
        UniqueConstraint("day", "port_id", "api_key_id", "model", name="uq_usage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (UTC)
    port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"), nullable=True, index=True)
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id"), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(160), default="")
    requests: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)


class Setting(Base):
    """Generic key/value settings store (JSON values)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
