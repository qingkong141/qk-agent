from typing import Any, Literal, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    agent_id: Optional[str] = None
    context: Optional[dict[str, Any]] = None
    execution_strategy: Literal["auto"] = "auto"
    approval_id: Optional[str] = None


class ChatResponse(BaseModel):
    conversation_id: str
    output: str
    status: str = "completed"
    intermediate_steps: list[dict[str, Any]] = []


class WsChatMessage(BaseModel):
    type: str = "chat"
    session_id: Optional[str] = None
    message: str
    agent_id: Optional[str] = None
    context: Optional[dict[str, Any]] = None
    execution_strategy: Literal["auto"] = "auto"
    approval_id: Optional[str] = None
