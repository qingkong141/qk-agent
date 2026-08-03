from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AgentConfigCreate(BaseModel):
    name: str
    role: str = "通用助手"
    capabilities: str = ""
    system_prompt: str = ""
    tools: list[str] = []
    is_default: bool = False


class AgentConfigUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    capabilities: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: Optional[list[str]] = None
    is_default: Optional[bool] = None


class AgentConfigResponse(BaseModel):
    id: str
    name: str
    role: str
    capabilities: str
    system_prompt: str
    tools: str
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationCreate(BaseModel):
    title: Optional[str] = "新对话"
    workspace: str = "default"


class ConversationResponse(BaseModel):
    id: str
    title: str
    workspace: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    sources: list[dict] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
