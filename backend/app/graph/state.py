from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


def merge_steps(left: list, right: list) -> list:
    return left + right


class WorkflowState(TypedDict):
    user_input: str
    chat_history: list[BaseMessage]
    user_id: str
    workspace: str
    agent_prompt: str
    agent_tools: list[str] | None
    turn_plan: dict
    response_status: str
    intent: str
    agent_result: str
    kb_hit: bool
    sources: list[dict]
    intermediate_steps: Annotated[list[Any], merge_steps]
    final_output: str
    # 临床决策字段
    action_type: str
    decision_id: str
    safety_level: str
