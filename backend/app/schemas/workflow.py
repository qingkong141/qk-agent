from typing import Any, Optional

from pydantic import BaseModel


class WorkflowInfo(BaseModel):
    id: str
    name: str
    description: str
    nodes: list[str]


class WorkflowExecuteRequest(BaseModel):
    input: dict[str, Any]
    conversation_id: Optional[str] = None


class WorkflowExecuteResponse(BaseModel):
    output: str
    intent: str
    intermediate_steps: list[dict[str, Any]] = []
