import pytest

from app.core.input_intent import (
    InputAction,
    InputIntent,
    build_clarification_message,
    build_resume_message,
    classify_user_input,
    is_interrupted_followup_instruction,
    is_high_risk_clinical_action,
    is_no_op_reply,
    is_resume_request,
    is_resumable_partial,
    is_short_clarification_reply,
    is_unclear_input,
)


def test_resume_request_detection_is_strict():
    assert is_resume_request("继续") is True
    assert is_resume_request("继续讲") is False
    assert is_resume_request("继续分析") is False


@pytest.mark.parametrize("text", ["继续", "继续说", "接着说", "接着讲", "继续生成", "继续回答"])
def test_resume_request_accepts_explicit_continue_commands(text):
    assert is_resume_request(text) is True


@pytest.mark.parametrize("text", ["继续吧", "接着", "继续分析", "不要继续了", "继续这个话题"])
def test_resume_request_rejects_ambiguous_continue_text(text):
    assert is_resume_request(text) is False


def test_short_or_placeholder_partial_is_not_resumable():
    assert is_resumable_partial("") is False
    assert is_resumable_partial("已停止") is False
    assert is_resumable_partial("刚开始") is False
    assert is_resumable_partial("我可以介绍基础医疗急救知识，先从呼救和现场安全说起。") is True


@pytest.mark.parametrize("text", ["", " ", "已停止", "已中止", "已取消", "刚开始", "根据参考资料"])
def test_non_meaningful_partial_is_not_resumable(text):
    assert is_resumable_partial(text) is False


@pytest.mark.parametrize("text", [
    "我可以介绍基础医疗急救知识，先从现场安全和呼救说起。",
    "哈根达斯是一个冰淇淋品牌，常见口味包括香草和巧克力。",
])
def test_meaningful_partial_is_resumable(text):
    assert is_resumable_partial(text) is True


def test_interrupted_followup_instruction_does_not_capture_new_short_topic():
    assert is_interrupted_followup_instruction("护士") is False
    assert is_interrupted_followup_instruction("哈根达斯") is False
    assert is_interrupted_followup_instruction("重点讲窒息") is True
    assert is_interrupted_followup_instruction("别讲品牌，换成价格") is True
    assert is_interrupted_followup_instruction("重新整理成三点") is True
    assert is_interrupted_followup_instruction("呼吸停止怎么办") is False


@pytest.mark.parametrize("text", [
    "重点讲窒息",
    "详细展开",
    "补充风险",
    "改成三点",
    "换成表格",
    "重新说",
    "别讲品牌，讲价格",
    "不要继续这个方向",
    "只讲中暑",
    "刚才那段太长了",
    "上面那个详细点",
])
def test_interrupted_followup_accepts_revision_or_followup_instructions(text):
    assert is_interrupted_followup_instruction(text) is True


@pytest.mark.parametrize("text", [
    "护士",
    "哈根达斯",
    "Python",
    "投资",
    "呼吸停止怎么办",
    "心跳停止怎么办",
    "停止出血怎么办",
    "护士的工作职责是什么",
])
def test_interrupted_followup_rejects_new_topics_and_stop_as_medical_term(text):
    assert is_interrupted_followup_instruction(text) is False


def test_short_clarification_reply_detection():
    assert is_short_clarification_reply("医生") is True
    assert is_short_clarification_reply("护理方向") is True
    assert is_short_clarification_reply("我想了解医生方向能做什么，顺便讲限制") is False
    assert is_short_clarification_reply("医生\n护士") is False


@pytest.mark.parametrize("text", ["医生", "护士", "医疗", "急救", "法律", "写代码", "Python", "投资", "学习", "都可以", "不知道"])
def test_short_clarification_reply_accepts_short_topic_answers(text):
    assert is_short_clarification_reply(text) is True


@pytest.mark.parametrize("text", [
    "",
    " ",
    "医生\n护士",
    "我想了解医生方向能做什么，顺便讲限制",
    "请详细介绍护士和医生的区别",
    "窒息怎么急救",
    "CPR 是什么",
])
def test_short_clarification_reply_rejects_empty_or_full_questions(text):
    assert is_short_clarification_reply(text) is False


def test_resume_message_continues_without_repeating_partial_output():
    prompt = build_resume_message({
        "user_message": "医疗急救",
        "partial_output": "我可以介绍基础急救原则。",
    }, "继续")

    assert "用户上一个问题：医疗急救" in prompt
    assert "我可以介绍基础急救原则。" in prompt
    assert "请从中断处自然继续，不要重复已经说过的内容。" in prompt


def test_resume_message_handles_follow_up_instruction():
    prompt = build_resume_message({
        "user_message": "医疗急救",
        "partial_output": "我可以介绍基础急救原则。",
    }, "重点讲窒息")

    assert "用户现在追加要求：重点讲窒息" in prompt
    assert "不要机械续写" in prompt


def test_clarification_message_binds_short_reply_to_previous_question():
    prompt = build_clarification_message({
        "user_message": "你会什么",
        "assistant_prompt": "请问您现在需要了解哪个方面的信息呢？",
    }, "医生")

    assert "用户上一个问题：你会什么" in prompt
    assert "用户现在补充：医生" in prompt
    assert "不要把这个短词当成独立问题" in prompt


@pytest.mark.parametrize("text, intent, allow_rag", [
    ("继续", InputIntent.RESUME_INTERRUPTED, False),
    ("没有", InputIntent.NO_OP, False),
    ("重点讲窒息", InputIntent.INTERRUPTED_FOLLOWUP, False),
    ("医生护士", InputIntent.MEDICAL_STAFF_CAPABILITY, False),
    ("护士医生", InputIntent.MEDICAL_STAFF_CAPABILITY, False),
    ("医生", InputIntent.DOCTOR_CAPABILITY, False),
    ("护士", InputIntent.NURSE_CAPABILITY, False),
    ("医疗急救", InputIntent.UNDERSPECIFIED_MEDICAL, False),
    ("调整滴速", InputIntent.HIGH_RISK_CLINICAL_ACTION, False),
    ("大师的撒刷到撒旦", InputIntent.UNCLEAR_INPUT, False),
    ("Python", InputIntent.GENERAL, True),
    ("你是谁", InputIntent.GENERAL, True),
    ("窒息怎么急救", InputIntent.GENERAL, True),
])
def test_classify_user_input_returns_intent_and_rag_policy(text, intent, allow_rag):
    result = classify_user_input(text)

    assert result.intent == intent
    assert result.allow_rag is allow_rag


def test_unclear_input_is_low_confidence_clarification():
    result = classify_user_input("大师的撒刷到撒旦")

    assert result.intent == InputIntent.UNCLEAR_INPUT
    assert result.action == InputAction.CLARIFY
    assert result.confidence < 0.5
    assert result.allow_rag is False


def test_unclear_input_heuristic_keeps_clear_queries_available():
    assert is_unclear_input("北京到上海高铁") is False
    assert is_unclear_input("窒息怎么急救") is False
    assert is_unclear_input("医生和护士区别") is False


def test_high_risk_clinical_action_requires_clarification():
    result = classify_user_input("调整滴速")

    assert is_high_risk_clinical_action("调整滴速") is True
    assert result.intent == InputIntent.HIGH_RISK_CLINICAL_ACTION
    assert result.action == InputAction.CLARIFY
    assert result.allow_rag is False


def test_no_op_reply_does_not_resume_or_follow_up():
    result = classify_user_input("没有", {
        "needs_user_input": {
            "assistant_message_id": "assistant-4",
            "user_message": "你是谁",
            "assistant_prompt": "有什么可以帮助你的吗？",
        },
    })

    assert is_no_op_reply("没有") is True
    assert is_interrupted_followup_instruction("没有") is False
    assert result.intent == InputIntent.NO_OP
    assert result.action == InputAction.DIRECT_REPLY
    assert result.allow_rag is False


def test_classify_user_input_builds_resume_action_with_context():
    result = classify_user_input("继续", {
        "interrupted": {
            "assistant_message_id": "assistant-1",
            "user_message": "医疗急救",
            "partial_output": "我可以介绍基础医疗急救知识，先从现场安全和呼救说起。",
        },
    })

    assert result.intent == InputIntent.RESUME_INTERRUPTED
    assert result.action == InputAction.RESUME
    assert result.resumed_from == "assistant-1"
    assert result.effective_message is not None
    assert "请从中断处自然继续" in result.effective_message


def test_classify_user_input_builds_clarification_effective_message_with_context():
    result = classify_user_input("医生", {
        "needs_user_input": {
            "assistant_message_id": "assistant-2",
            "user_message": "你会什么",
            "assistant_prompt": "请问您现在需要了解哪个方面的信息呢？",
        },
    })

    assert result.intent == InputIntent.DOCTOR_CAPABILITY
    assert result.action == InputAction.DIRECT_REPLY


def test_classify_short_unknown_topic_binds_to_pending_clarification():
    result = classify_user_input("Python", {
        "needs_user_input": {
            "assistant_message_id": "assistant-3",
            "user_message": "你会什么",
            "assistant_prompt": "请问您现在需要了解哪个方面的信息呢？",
        },
    })

    assert result.intent == InputIntent.SHORT_CLARIFICATION
    assert result.clarified_from == "assistant-3"
    assert result.effective_message is not None
    assert "用户现在补充：Python" in result.effective_message


@pytest.mark.parametrize("text", ["你是谁", "他是谁", "你在哪", "选哪个", "多少钱", "几点了", "能不能用"])
def test_standalone_short_requests_route_as_general_input(text):
    result = classify_user_input(text)

    assert result.intent == InputIntent.GENERAL
    assert result.action == InputAction.ROUTE
    assert result.allow_rag is True
