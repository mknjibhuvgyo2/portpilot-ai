"""Pydantic schemas for port services."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class PortCreate(BaseModel):
    name: str
    slug: str
    port: int = Field(ge=1, le=65535)
    app_type: str = "generic_chat"
    model_alias: str = ""
    system_prompt: str = ""
    streaming: bool = True
    concurrency: int = Field(default=8, ge=1, le=256)
    timeout: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    logging_enabled: bool = True
    log_keep: int = Field(default=10, ge=0, le=1000)
    auth_required: bool = False
    autostart: bool = False

    @field_validator("slug")
    @classmethod
    def _slug_ok(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]*", v):
            raise ValueError("slug must be lowercase alphanumeric, '-' or '_'")
        return v


class PortUpdate(BaseModel):
    name: str | None = None
    model_alias: str | None = None
    system_prompt: str | None = None
    streaming: bool | None = None
    concurrency: int | None = Field(default=None, ge=1, le=256)
    timeout: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    logging_enabled: bool | None = None
    log_keep: int | None = Field(default=None, ge=0, le=1000)
    auth_required: bool | None = None
    autostart: bool | None = None


class PortOut(BaseModel):
    id: int
    name: str
    slug: str
    port: int
    app_type: str
    model_alias: str
    system_prompt: str
    streaming: bool
    concurrency: int
    timeout: float
    max_retries: int
    logging_enabled: bool
    log_keep: int
    auth_required: bool
    autostart: bool
    status: str

    class Config:
        from_attributes = True
