from dataclasses import dataclass
from enum import StrEnum
import re

from app.core.prompts import extract_user_input


class InputIntent(StrEnum):
    GENERAL = "general"
    NO_OP = "no_op"
    RESUME_INTERRUPTED = "resume_interrupted"
    INTERRUPTED_FOLLOWUP = "interrupted_followup"
    SHORT_CLARIFICATION = "short_clarification"
    DOCTOR_CAPABILITY = "doctor_capability"
    NURSE_CAPABILITY = "nurse_capability"
    MEDICAL_STAFF_CAPABILITY = "medical_staff_capability"
    UNDERSPECIFIED_MEDICAL = "underspecified_medical"
    UNCLEAR_INPUT = "unclear_input"
    HIGH_RISK_CLINICAL_ACTION = "high_risk_clinical_action"


class InputAction(StrEnum):
    ROUTE = "route"
    DIRECT_REPLY = "direct_reply"
    CLARIFY = "clarify"
    RESUME = "resume"


@dataclass(frozen=True)
class InputIntentResult:
    intent: InputIntent
    allow_rag: bool = True
    action: InputAction = InputAction.ROUTE
    confidence: float = 1.0
    effective_message: str | None = None
    resumed_from: str | None = None
    clarified_from: str | None = None


def normalize_short_text(text: str) -> str:
    return extract_user_input(text).strip().replace(" ", "")


def is_resume_request(message: str) -> bool:
    return message.strip() in {"继续", "继续说", "接着说", "接着讲", "继续生成", "继续回答"}


def is_no_op_reply(message: str) -> bool:
    return normalize_short_text(message) in {
        "没有",
        "没了",
        "没事",
        "没事了",
        "不用",
        "不用了",
        "不需要",
        "暂时没有",
        "先不用",
    }


def is_social_expression(message: str) -> bool:
    text = normalize_short_text(message)
    if not text or len(text) > 20:
        return False
    if re.fullmatch(r"[哈呵嘿嘻笑]+[！!。~～]*", text):
        return True
    if "不舒服" in text:
        return False
    positive_markers = (
        "舒服",
        "开心",
        "高兴",
        "太好了",
        "真不错",
        "真棒",
        "有意思",
        "好玩",
    )
    return any(marker in text for marker in positive_markers)


def is_interrupted_followup_instruction(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    markers = (
        "重点",
        "详细",
        "展开",
        "补充",
        "改成",
        "换成",
        "重新",
        "别",
        "不要",
        "只讲",
        "只说",
        "上面",
        "刚才",
        "前面",
        "这段",
        "那个",
        "接着",
    )
    return any(marker in text for marker in markers)


def is_resumable_partial(output: str) -> bool:
    text = output.strip()
    return len(text) >= 20 and text not in {"已停止", "已中止"}


def is_short_clarification_reply(message: str) -> bool:
    text = message.strip()
    question_markers = ("怎么", "如何", "什么", "为何", "为什么", "吗", "？", "?")
    return 0 < len(text) <= 12 and "\n" not in text and not any(marker in text for marker in question_markers)


def is_underspecified_medical_query(text: str) -> bool:
    return normalize_short_text(text) in {"医生", "医疗", "医学", "急救", "医疗急救", "急救知识", "医疗知识"}


def is_unclear_input(message: str) -> bool:
    text = normalize_short_text(message)
    if len(text) < 8 or len(text) > 30 or "\n" in message:
        return False
    question_markers = ("怎么", "如何", "什么", "为何", "为什么", "吗", "？", "?")
    if any(marker in text for marker in question_markers):
        return False
    connectors = ("的", "地", "得", "到", "在", "和", "与", "及", "跟")
    connector_count = sum(text.count(connector) for connector in connectors)
    if connector_count < 2:
        return False
    known_domain_markers = (
        "医生",
        "护士",
        "医疗",
        "医学",
        "急救",
        "护理",
        "高铁",
        "火车",
        "机票",
        "航班",
        "天气",
        "价格",
    )
    return not any(marker in text for marker in known_domain_markers)


def is_high_risk_clinical_action(message: str) -> bool:
    text = normalize_short_text(message)
    infusion_action_markers = (
        "调整滴速",
        "调滴速",
        "调节滴速",
        "输液滴速",
        "输液速度",
        "调输液",
        "滴速调整",
    )
    if any(marker in text for marker in infusion_action_markers):
        return True
    if "滴速" not in text:
        return False
    return bool(re.search(r"(调|改|加|减|升|降|到|至|设|设置).*\d|\d+.*(调|改|加|减|升|降|到|至|设|设置)", text))


def is_doctor_capability_query(text: str) -> bool:
    return normalize_short_text(text) in {"医生", "医疗方向", "医生方向", "医疗能力", "医学方向"}


def is_nurse_capability_query(text: str) -> bool:
    return normalize_short_text(text) in {"护士", "护理", "护士方向", "护理方向"}


def is_medical_staff_capability_query(text: str) -> bool:
    return normalize_short_text(text) in {
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
    }


def is_capability_clarification_for_doctor(text: str) -> bool:
    query = extract_user_input(text)
    return "用户上一个问题：你会什么" in query and "用户现在补充：医生" in query


def build_resume_message(interrupted: dict, user_message: str) -> str:
    previous_question = interrupted.get("user_message", "")
    partial_output = interrupted.get("partial_output", "")
    if is_resume_request(user_message):
        return (
            f"用户上一个问题：{previous_question}\n\n"
            f"你刚才回答到这里被用户停止：\n{partial_output}\n\n"
            "请从中断处自然继续，不要重复已经说过的内容。"
        )
    return (
        f"用户上一个问题：{previous_question}\n\n"
        f"你刚才未完成的回答：\n{partial_output}\n\n"
        f"用户现在追加要求：{user_message}\n\n"
        "请基于追加要求回答；如果是在修正方向，可以重新组织答案，不要机械续写。"
    )


def build_clarification_message(pending_turn: dict, user_message: str) -> str:
    return (
        f"用户上一个问题：{pending_turn.get('user_message', '')}\n\n"
        f"你刚才为了澄清而追问：{pending_turn.get('assistant_prompt', '')}\n\n"
        f"用户现在补充：{user_message}\n\n"
        "请把用户补充理解为对上一条澄清问题的回答，继续完成原任务。"
        "不要把这个短词当成独立问题，也不要检索无关资料。"
    )


def classify_user_input(message: str, conversation_state: dict | None = None) -> InputIntentResult:
    conversation_state = conversation_state or {}
    if is_no_op_reply(message):
        return InputIntentResult(InputIntent.NO_OP, allow_rag=False, action=InputAction.DIRECT_REPLY)
    if is_resume_request(message):
        interrupted = conversation_state.get("interrupted")
        if interrupted and is_resumable_partial(interrupted.get("partial_output", "")):
            return InputIntentResult(
                InputIntent.RESUME_INTERRUPTED,
                allow_rag=False,
                action=InputAction.RESUME,
                effective_message=build_resume_message(interrupted, message),
                resumed_from=interrupted.get("assistant_message_id"),
            )
        return InputIntentResult(InputIntent.RESUME_INTERRUPTED, allow_rag=False, action=InputAction.CLARIFY)
    if is_interrupted_followup_instruction(message):
        interrupted = conversation_state.get("interrupted")
        if interrupted and is_resumable_partial(interrupted.get("partial_output", "")):
            return InputIntentResult(
                InputIntent.INTERRUPTED_FOLLOWUP,
                allow_rag=False,
                action=InputAction.RESUME,
                effective_message=build_resume_message(interrupted, message),
                resumed_from=interrupted.get("assistant_message_id"),
            )
        return InputIntentResult(
            InputIntent.INTERRUPTED_FOLLOWUP,
            allow_rag=False,
            action=InputAction.CLARIFY,
        )
    if is_medical_staff_capability_query(message):
        return InputIntentResult(InputIntent.MEDICAL_STAFF_CAPABILITY, allow_rag=False, action=InputAction.DIRECT_REPLY)
    if is_doctor_capability_query(message) or is_capability_clarification_for_doctor(message):
        return InputIntentResult(InputIntent.DOCTOR_CAPABILITY, allow_rag=False, action=InputAction.DIRECT_REPLY)
    if is_nurse_capability_query(message):
        return InputIntentResult(InputIntent.NURSE_CAPABILITY, allow_rag=False, action=InputAction.DIRECT_REPLY)
    if is_underspecified_medical_query(message):
        return InputIntentResult(InputIntent.UNDERSPECIFIED_MEDICAL, allow_rag=False, action=InputAction.CLARIFY)
    if is_high_risk_clinical_action(message):
        return InputIntentResult(
            InputIntent.HIGH_RISK_CLINICAL_ACTION,
            allow_rag=False,
            action=InputAction.CLARIFY,
        )
    if is_unclear_input(message):
        return InputIntentResult(
            InputIntent.UNCLEAR_INPUT,
            allow_rag=False,
            action=InputAction.CLARIFY,
            confidence=0.35,
        )
    if is_social_expression(message):
        return InputIntentResult(InputIntent.GENERAL, allow_rag=False)
    pending_turn = conversation_state.get("needs_user_input")
    if pending_turn and is_short_clarification_reply(message):
        return InputIntentResult(
            InputIntent.SHORT_CLARIFICATION,
            allow_rag=False,
            action=InputAction.ROUTE,
            effective_message=build_clarification_message(pending_turn, message),
            clarified_from=pending_turn.get("assistant_message_id"),
        )
    return InputIntentResult(InputIntent.GENERAL, allow_rag=True)
