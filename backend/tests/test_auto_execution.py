import pytest

from app.api import chat
from app.core.turn_planner import TurnPlan
from app.schemas.chat import ChatRequest, WsChatMessage


class _Workflow:
    def __init__(self):
        self.state = None

    async def ainvoke(self, state):
        self.state = state
        return {"final_output": "ok"}


@pytest.mark.asyncio
async def test_auto_workflow_receives_selected_main_agent_config(monkeypatch):
    workflow = _Workflow()
    monkeypatch.setattr(chat, "get_workflow", lambda: workflow)

    result = await chat._run_workflow(
        "hello",
        [],
        "user-1",
        "default",
        turn_plan=TurnPlan(effective_message="hello"),
        agent_prompt="selected prompt",
        agent_tools=["calculator"],
    )

    assert result["output"] == "ok"
    assert workflow.state["agent_prompt"] == "selected prompt"
    assert workflow.state["agent_tools"] == ["calculator"]


def test_chat_protocol_only_accepts_auto_strategy():
    assert ChatRequest(message="hello").execution_strategy == "auto"
    assert WsChatMessage(message="hello").execution_strategy == "auto"

    with pytest.raises(ValueError):
        ChatRequest(message="hello", execution_strategy="direct")
