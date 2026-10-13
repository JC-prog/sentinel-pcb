"""
A2A (Agent-to-Agent) Protocol Data Schemas.
Compatible with HTTP / JSON-RPC 2.0 and Agent Card discovery.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)


class AgentCard(BaseModel):
    name: str
    endpoint: str
    description: str
    version: str = "2.1.0"
    skills: List[AgentSkill] = Field(default_factory=list)


class A2ATaskRequest(BaseModel):
    task_id: Optional[str] = None
    skill_id: Optional[str] = None
    task_type: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None


class A2ATaskResponse(BaseModel):
    task_id: str
    state: str  # "completed" | "failed" | "in_progress"
    result: Dict[str, Any] = Field(default_factory=dict)
    artifact: Optional[Dict[str, Any]] = Field(default_factory=dict)