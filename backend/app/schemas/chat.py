from typing import Any, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    context: Optional[dict[str, Any]] = None
    use_workflow: bool = True


class ChatResponse(BaseModel):
    conversation_id: str
    output: str
    intermediate_steps: list[dict[str, Any]] = []


class WsChatMessage(BaseModel):
    type: str = "chat"
    session_id: Optional[str] = None
    message: str
    context: Optional[dict[str, Any]] = None
    use_workflow: bool = True
