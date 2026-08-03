from pydantic import BaseModel


class ToolUsageItem(BaseModel):
    tool: str
    count: int


class MetricsResponse(BaseModel):
    total_conversations: int
    total_messages: int
    tool_calls_today: int
    tokens_used_today: int
    tool_usage: list[ToolUsageItem] = []
