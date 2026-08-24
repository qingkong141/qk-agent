import pytest

from app.core.input_intent import InputAction
from app.core.turn_planner import plan_turn
from app.graph.nodes import kb_agent_node, route_intent


@pytest.mark.asyncio
async def test_small_talk_plan_disables_rag_without_semantic_routing(monkeypatch):
    async def fail_semantic_router(message):
        raise AssertionError("social expression should not invoke semantic routing")

    monkeypatch.setattr("app.core.turn_planner._classify_workflow_intent", fail_semantic_router)

    plan = await plan_turn("非常的舒服")

    assert plan.intent == "general"
    assert plan.allow_rag is False
    assert plan.allow_side_effects is False
    assert plan.direct_response is None


@pytest.mark.asyncio
async def test_high_risk_plan_blocks_execution_before_workflow(monkeypatch):
    async def fail_semantic_router(message):
        raise AssertionError("known high-risk action should be blocked deterministically")

    monkeypatch.setattr("app.core.turn_planner._classify_workflow_intent", fail_semantic_router)

    plan = await plan_turn("调整滴速")

    assert plan.intent == "general"
    assert plan.action == InputAction.CLARIFY
    assert plan.allow_side_effects is False
    assert plan.needs_user_input is True
    assert plan.risk_level == "high"
    assert plan.requires_approval is True
    assert "不要自行调节" in plan.direct_response


@pytest.mark.asyncio
async def test_semantic_knowledge_plan_is_the_only_way_to_enable_rag(monkeypatch):
    async def knowledge_router(message):
        return "knowledge_query"

    monkeypatch.setattr("app.core.turn_planner._classify_workflow_intent", knowledge_router)

    plan = await plan_turn("窒息时应该怎么办？")

    assert plan.intent == "knowledge_query"
    assert plan.allow_rag is True
    assert plan.allow_side_effects is False


@pytest.mark.asyncio
async def test_clarification_status_is_structured_not_inferred_from_text():
    plan = await plan_turn("拉布布")

    assert plan.needs_user_input is True
    assert plan.direct_response


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["医疗急救", "重点讲窒息"])
async def test_known_follow_up_prompts_explicitly_require_user_input(message):
    plan = await plan_turn(message)

    assert plan.action == InputAction.CLARIFY
    assert plan.needs_user_input is True
    assert plan.direct_response


@pytest.mark.parametrize("intent", ["infusion_adjust", "education_recommend"])
def test_side_effect_routes_are_blocked_without_explicit_permission(intent):
    assert route_intent({
        "turn_plan": {
            "intent": intent,
            "allow_side_effects": False,
            "allow_rag": False,
        },
    }) == "response"


def test_rag_route_is_blocked_without_explicit_permission():
    assert route_intent({
        "turn_plan": {
            "intent": "knowledge_query",
            "allow_side_effects": False,
            "allow_rag": False,
        },
    }) == "response"


@pytest.mark.asyncio
async def test_kb_node_cannot_search_when_plan_disables_rag(monkeypatch):
    def fail_search(query):
        raise AssertionError("knowledge base must not run when allow_rag is false")

    monkeypatch.setattr("app.graph.nodes.rag_pipeline.resolve_search", fail_search)

    result = await kb_agent_node({
        "turn_plan": {"allow_rag": False},
        "user_input": "闲聊",
    })

    assert result == {"agent_result": "", "kb_hit": False, "sources": []}
