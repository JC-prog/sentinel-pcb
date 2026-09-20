"""Ported verbatim from orchestrator-agent/adc_agentic_project's services/common.py."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServiceResult:
    success: bool
    status: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    recoverable: bool = False
    next_action: str | None = None
