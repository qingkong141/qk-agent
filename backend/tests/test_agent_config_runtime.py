from types import SimpleNamespace

import pytest

from app.core import agent_factory


class _Result:
    def __init__(self, config):
        self.config = config

    def scalar_one_or_none(self):
        return self.config


class _FakeDb:
    def __init__(self, config):
        self.config = config
        self.requested_id = None
        self.executed_default_query = False

    async def get(self, model, agent_id):
        self.requested_id = agent_id
        return self.config

    async def execute(self, statement):
        self.executed_default_query = True
        return _Result(self.config)


@pytest.mark.asyncio
async def test_create_agent_executor_from_db_uses_selected_config(monkeypatch):
    captured = {}

    def fake_create_agent_executor(**kwargs):
        captured.update(kwargs)
        return "agent"

    monkeypatch.setattr(agent_factory, "create_agent_executor", fake_create_agent_executor)
    config = SimpleNamespace(
        role="clinical assistant",
        capabilities="answer with clinical context",
        system_prompt="custom system prompt",
        tools='["calculator", "search_knowledge_base"]',
    )
    db = _FakeDb(config)

    agent = await agent_factory.create_agent_executor_from_db(
        db,
        agent_id="agent-1",
        streaming=True,
    )

    assert agent == "agent"
    assert db.requested_id == "agent-1"
    assert captured == {
        "role": "clinical assistant",
        "capabilities": "answer with clinical context",
        "system_prompt": "custom system prompt",
        "tool_names": ["calculator", "search_knowledge_base"],
        "streaming": True,
    }


@pytest.mark.asyncio
async def test_create_agent_executor_from_db_uses_default_config(monkeypatch):
    captured = {}

    def fake_create_agent_executor(**kwargs):
        captured.update(kwargs)
        return "agent"

    monkeypatch.setattr(agent_factory, "create_agent_executor", fake_create_agent_executor)
    config = SimpleNamespace(
        role="default role",
        capabilities="default capabilities",
        system_prompt="",
        tools="[]",
    )
    db = _FakeDb(config)

    await agent_factory.create_agent_executor_from_db(db)

    assert db.executed_default_query is True
    assert captured["role"] == "default role"
    assert captured["capabilities"] == "default capabilities"
    assert captured["system_prompt"] is None
    assert captured["tool_names"] == []


@pytest.mark.asyncio
async def test_load_agent_runtime_config_keeps_prompt_and_tools_together():
    config = SimpleNamespace(
        role="nurse assistant",
        capabilities="explain nursing knowledge",
        system_prompt="configured main agent",
        tools='["calculator"]',
    )
    db = _FakeDb(config)

    prompt, tools = await agent_factory.load_agent_runtime_config(db, "agent-1")

    assert db.requested_id == "agent-1"
    assert prompt == "configured main agent"
    assert tools == ["calculator"]
