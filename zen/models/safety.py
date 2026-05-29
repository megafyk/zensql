"""Safety-violation model used by intent guard, validator, and audit log."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SafetyViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)
