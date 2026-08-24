import pytest

from app.core.input_intent import InputIntent
from app.core.intent_handlers import resolve_intent_response


@pytest.mark.parametrize("intent, expected", [
    (InputIntent.DOCTOR_CAPABILITY, "医疗方向能做什么"),
    (InputIntent.NO_OP, "随时告诉我"),
    (InputIntent.NURSE_CAPABILITY, "护士或护理方向"),
    (InputIntent.MEDICAL_STAFF_CAPABILITY, "医生和护士相关内容"),
    (InputIntent.UNDERSPECIFIED_MEDICAL, "你想了解哪一类场景"),
    (InputIntent.HIGH_RISK_CLINICAL_ACTION, "高风险医疗/护理操作"),
])
def test_resolve_intent_response_handles_fixed_response_intents(intent, expected):
    response = resolve_intent_response(intent)

    assert response is not None
    assert expected in response


@pytest.mark.parametrize("intent", [
    InputIntent.GENERAL,
])
def test_resolve_intent_response_leaves_runtime_context_intents_to_workflow(intent):
    assert resolve_intent_response(intent) is None


def test_resolve_intent_response_clarifies_unknown_short_topic():
    assert resolve_intent_response(InputIntent.SHORT_CLARIFICATION, "拉布布") == (
        "你想了解“拉布布”的哪方面？可以补充一下具体问题。"
    )


def test_resolve_intent_response_confirms_unclear_input():
    response = resolve_intent_response(InputIntent.UNCLEAR_INPUT, "大师的撒刷到撒旦")

    assert response == "我不太确定你想问的是“大师的撒刷到撒旦”哪方面。可以换个说法，或补充你想了解的对象/问题吗？"


def test_resolve_intent_response_handles_resume_without_context():
    assert "没有可继续" in resolve_intent_response(InputIntent.RESUME_INTERRUPTED, "继续")
