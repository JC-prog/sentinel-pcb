# src/a2a/protocol.py
from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel

TaskState = Literal["submitted", "working", "completed", "failed"]

class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]

class AgentCard(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str
    endpoint: str
    protocol: str = "A2A-v1.0"
    skills: List[AgentSkill]

class A2ATaskRequest(BaseModel):
    task_id: str
    skill_id: str
    input_data: Dict[str, Any]

class A2ATaskResponse(BaseModel):
    task_id: str
    state: TaskState
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
