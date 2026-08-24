import pytest

from app.core.input_intent import (
    is_capability_clarification_for_doctor,
    is_doctor_capability_query,
    is_high_risk_clinical_action,
    is_medical_staff_capability_query,
    is_nurse_capability_query,
    is_social_expression,
    is_underspecified_medical_query,
    is_unclear_input,
)
from app.graph.nodes import response_agent_node
from app.core.turn_planner import plan_turn


def test_underspecified_medical_query_requires_clarification():
    assert is_underspecified_medical_query("医疗") is True
    assert is_underspecified_medical_query("急救") is True
    assert is_underspecified_medical_query("医疗急救") is True
    assert is_underspecified_medical_query("医生") is True
    assert is_underspecified_medical_query("医学") is True


@pytest.mark.parametrize("text", [
    "医疗",
    "医学",
    "急救",
    "医疗急救",
    "急救知识",
    "医疗知识",
    "医生",
])
def test_underspecified_medical_queries_do_not_go_directly_to_rag(text):
    assert is_underspecified_medical_query(text) is True


def test_specific_medical_query_can_continue_to_rag():
    assert is_underspecified_medical_query("窒息怎么急救") is False
    assert is_underspecified_medical_query("中暑怎么办") is False
    assert is_underspecified_medical_query("护士的工作职责是什么") is False


def test_unclear_fragment_requires_confirmation():
    assert is_unclear_input("大师的撒刷到撒旦") is True
    assert is_unclear_input("北京到上海高铁") is False
    assert is_unclear_input("窒息怎么急救") is False


@pytest.mark.parametrize("text", ["哈哈哈哈", "非常的舒服", "太好了", "真不错"])
def test_social_expressions_are_not_short_topic_clarifications(text):
    assert is_social_expression(text) is True


def test_high_risk_clinical_action_is_not_general_advice():
    assert is_high_risk_clinical_action("调整滴速") is True
    assert is_high_risk_clinical_action("把3床滴速调到40") is True
    assert is_high_risk_clinical_action("滴速多少") is False


@pytest.mark.parametrize("text", [
    "窒息怎么急救",
    "中暑怎么办",
    "一氧化碳中毒怎么办",
    "心肺复苏怎么做",
    "CPR 是什么",
    "呼吸停止怎么办",
    "心跳停止怎么办",
    "被刀扎了怎么办",
    "要不要打破伤风",
    "心绞痛怎么办",
])
def test_specific_medical_queries_are_not_blocked_as_underspecified(text):
    assert is_underspecified_medical_query(text) is False


def test_doctor_capability_query_does_not_enter_rag():
    assert is_doctor_capability_query("医生") is True
    assert is_nurse_capability_query("护士") is True
    assert is_nurse_capability_query("护理方向") is True
    assert is_doctor_capability_query("医疗方向") is True
    assert is_doctor_capability_query("医生能开药吗") is False


@pytest.mark.parametrize("text", ["医生", "护士", "护理", "医疗方向", "医生方向", "护士方向", "护理方向", "医疗能力", "医学方向", "医生护士", "护士医生", "医护"])
def test_medical_capability_shortcuts_do_not_enter_rag(text):
    assert (
        is_doctor_capability_query(text)
        or is_nurse_capability_query(text)
        or is_medical_staff_capability_query(text)
    ) is True


@pytest.mark.parametrize("text", ["医生", "医疗方向", "医生方向", "医疗能力", "医学方向"])
def test_doctor_capability_query_is_separate_from_nurse(text):
    assert is_doctor_capability_query(text) is True
    assert is_nurse_capability_query(text) is False


@pytest.mark.parametrize("text", ["护士", "护理", "护士方向", "护理方向"])
def test_nurse_capability_query_is_separate_from_doctor(text):
    assert is_nurse_capability_query(text) is True
    assert is_doctor_capability_query(text) is False


@pytest.mark.parametrize("text", [
    "医生护士",
    "护士医生",
    "医生和护士",
    "护士和医生",
    "医生与护士",
    "护士与医生",
    "医护",
    "医护人员",
    "医生护士区别",
    "护士医生区别",
    "医生和护士区别",
    "护士和医生区别",
])
def test_medical_staff_capability_query_handles_combined_shortcuts(text):
    assert is_medical_staff_capability_query(text) is True
    assert is_doctor_capability_query(text) is False
    assert is_nurse_capability_query(text) is False


@pytest.mark.parametrize("text", ["医生能开药吗", "护士的工作职责是什么", "护理专业怎么学", "医生和护士区别"])
def test_specific_medical_profession_questions_are_not_capability_shortcuts(text):
    assert is_doctor_capability_query(text) is False
    assert is_nurse_capability_query(text) is False


def test_capability_clarification_for_doctor_is_bound_to_previous_question():
    assert is_capability_clarification_for_doctor(
        "用户上一个问题：你会什么\n\n用户现在补充：医生"
    ) is True
    assert is_capability_clarification_for_doctor("用户现在补充：医生") is False
    assert is_capability_clarification_for_doctor(
        "用户上一个问题：医疗急救\n\n用户现在补充：医生"
    ) is False


@pytest.mark.asyncio
async def test_doctor_capability_response_does_not_attach_rag_sources(monkeypatch):
    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)
    plan = await plan_turn("医生")
    result = await response_agent_node({
        "turn_plan": plan.model_dump(mode="json"),
        "user_input": "医生",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [{"source": "医疗急救小常识.pdf"}],
        "user_id": "test-user",
    })

    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert "医疗方向能做什么" in result["final_output"]


@pytest.mark.asyncio
async def test_nurse_capability_response_is_nursing_specific(monkeypatch):
    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)
    plan = await plan_turn("护士")
    result = await response_agent_node({
        "turn_plan": plan.model_dump(mode="json"),
        "user_input": "护士",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [{"source": "医疗急救小常识.pdf"}],
        "user_id": "test-user",
    })

    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert "护士或护理方向" in result["final_output"]
    assert "护理岗位常见职责" in result["final_output"]


@pytest.mark.asyncio
async def test_medical_staff_capability_response_is_not_emergency_rag(monkeypatch):
    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)
    plan = await plan_turn("护士医生")
    result = await response_agent_node({
        "turn_plan": plan.model_dump(mode="json"),
        "user_input": "护士医生",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [{"source": "医疗急救小常识.pdf"}],
        "user_id": "test-user",
    })

    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert "医生的诊疗职责" in result["final_output"]
    assert "护士的护理职责" in result["final_output"]


@pytest.mark.asyncio
async def test_unknown_short_topic_clarifies_without_rag_sources(monkeypatch):
    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)
    plan = await plan_turn("拉布布")
    result = await response_agent_node({
        "turn_plan": plan.model_dump(mode="json"),
        "user_input": "拉布布",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [{"source": "医疗急救小常识.pdf"}],
        "user_id": "test-user",
    })

    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert result["final_output"] == "你想了解“拉布布”的哪方面？可以补充一下具体问题。"


@pytest.mark.asyncio
async def test_unclear_input_confirms_before_answering(monkeypatch):
    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)
    plan = await plan_turn("大师的撒刷到撒旦")
    result = await response_agent_node({
        "turn_plan": plan.model_dump(mode="json"),
        "user_input": "大师的撒刷到撒旦",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [{"source": "医疗急救小常识.pdf"}],
        "user_id": "test-user",
    })

    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert result["final_output"] == "我不太确定你想问的是“大师的撒刷到撒旦”哪方面。可以换个说法，或补充你想了解的对象/问题吗？"


@pytest.mark.asyncio
async def test_high_risk_clinical_action_does_not_return_steps_or_sources(monkeypatch):
    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)
    plan = await plan_turn("调整滴速")
    result = await response_agent_node({
        "turn_plan": plan.model_dump(mode="json"),
        "user_input": "调整滴速",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [{"source": "医疗急救小常识.pdf"}],
        "user_id": "test-user",
    })

    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert "高风险医疗/护理操作" in result["final_output"]
    assert "不要自行调节" in result["final_output"]
    assert "具体操作步骤" not in result["final_output"]


@pytest.mark.asyncio
async def test_no_op_reply_closes_without_resume_message_or_sources(monkeypatch):
    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)
    plan = await plan_turn("没有")
    result = await response_agent_node({
        "turn_plan": plan.model_dump(mode="json"),
        "user_input": "没有",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [{"source": "医疗急救小常识.pdf"}],
        "user_id": "test-user",
    })

    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert result["final_output"] == "好的，有需要随时告诉我。"


@pytest.mark.asyncio
async def test_small_talk_does_not_search_or_attach_knowledge_sources(monkeypatch):
    captured = {}

    class _Agent:
        async def ainvoke(self, payload):
            return {"output": "那就好，舒服地享受一下。", "intermediate_steps": []}

    def fake_create_agent_executor(**kwargs):
        captured.update(kwargs)
        return _Agent()

    def fail_search(*args, **kwargs):
        raise AssertionError("small talk must not search the knowledge base")

    async def fake_log_usage(*args, **kwargs):
        return None

    monkeypatch.setattr("app.graph.nodes.create_agent_executor", fake_create_agent_executor)
    monkeypatch.setattr("app.graph.nodes.rag_pipeline.resolve_search", fail_search)
    monkeypatch.setattr("app.graph.nodes.log_usage", fake_log_usage)

    result = await response_agent_node({
        "user_input": "非常的舒服",
        "intent": "general",
        "agent_result": "",
        "kb_hit": False,
        "sources": [],
        "agent_tools": ["search_knowledge_base", "calculator"],
        "chat_history": [],
        "user_id": "test-user",
    })

    assert result["final_output"] == "那就好，舒服地享受一下。"
    assert result["kb_hit"] is False
    assert result["sources"] == []
    assert captured["tool_names"] == ["calculator"]
