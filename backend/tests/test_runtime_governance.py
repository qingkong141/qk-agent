import uuid

import pytest
from langchain_core.tools import StructuredTool
from sqlalchemy import select

from app.core.context import set_request_context
from app.db.session import async_session
from app.models.runtime import ToolApproval
from app.services.clinical_decision_service import confirm_decision, create_decision
from app.services.runtime_state import (
    approve_tool,
    get_user_context,
    last_checkpoint,
    save_checkpoint,
    set_user_context,
)
from app.tools.registry import tool_manager


@pytest.mark.asyncio
async def test_checkpoint_is_persistent_and_owner_scoped():
    run_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    async with async_session() as db:
        await save_checkpoint(
            db, run_id=run_id, conversation_id=conversation_id, owner_id="owner-a",
            external_user_id="customer-a", phase="planned", status="running",
            state={"step": 1},
        )
    async with async_session() as db:
        own = await last_checkpoint(db, run_id, "owner-a", "customer-a")
        foreign = await last_checkpoint(db, run_id, "owner-a", "customer-b")
    assert own is not None and own.state == {"step": 1}
    assert foreign is None


@pytest.mark.asyncio
async def test_context_is_persistent_and_end_user_scoped():
    workspace = f"workspace-{uuid.uuid4()}"
    async with async_session() as db:
        await set_user_context(db, "app", "customer-a", workspace, {"page": "ward"})
    async with async_session() as db:
        assert await get_user_context(db, "app", "customer-a", workspace) == {"page": "ward"}
        assert await get_user_context(db, "app", "customer-b", workspace) == {}


@pytest.mark.asyncio
async def test_side_effect_tool_requires_matching_approval():
    calls = []

    async def execute(value: str) -> str:
        calls.append(value)
        return "executed"

    tool_manager.register(StructuredTool.from_function(
        coroutine=execute, name="confirm_education_push", description="test",
    ))
    conversation_id = str(uuid.uuid4())
    set_request_context("app", external_user_id="customer", conversation_id=conversation_id, run_id="run")
    tool = tool_manager.get("confirm_education_push")
    blocked = await tool.ainvoke({"value": "K001"})
    assert "approval_id" in blocked
    assert calls == []

    async with async_session() as db:
        approval = await db.scalar(select(ToolApproval).where(
            ToolApproval.conversation_id == conversation_id,
            ToolApproval.tool_name == "confirm_education_push",
        ))
        await approve_tool(db, approval, "app")
    set_request_context(
        "app", external_user_id="customer", conversation_id=conversation_id,
        run_id="run", approval_id=approval.id,
    )
    assert await tool.ainvoke({"value": "K001"}) == "executed"
    assert calls == ["K001"]
    assert "不会重复执行" in await tool.ainvoke({"value": "K001"})
    assert calls == ["K001"]


@pytest.mark.asyncio
async def test_clinical_decision_is_idempotent_and_executes_once():
    set_request_context("app", external_user_id="customer")
    key = str(uuid.uuid4())
    params = {"items": [{"id": "rate", "status": "pending"}],
              "risk_assessment": {"blocked": False}}
    first = await create_decision(
        action_type="infusion_adjust", patient_id="P001", trigger_source="test",
        trigger_text="adjust", action_params=params, idempotency_key=key,
    )
    duplicate = await create_decision(
        action_type="infusion_adjust", patient_id="P001", trigger_source="test",
        trigger_text="adjust", action_params=params, idempotency_key=key,
    )
    assert duplicate.id == first.id

    calls = []

    async def execute(item, decision):
        calls.append(item["id"])

    result = await confirm_decision(
        first.id, ["rate"], execute_fn=execute, confirmed_by="app",
        owner_id="app", external_user_id="customer",
    )
    assert result["status"] == "resolved"
    with pytest.raises(ValueError):
        await confirm_decision(
            first.id, ["rate"], execute_fn=execute, confirmed_by="app",
            owner_id="app", external_user_id="customer",
        )
    assert calls == ["rate"]
