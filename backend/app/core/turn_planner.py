from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.core.input_intent import InputAction, InputIntent, classify_user_input
from app.core.intent_handlers import resolve_intent_response
from app.core.prompts import extract_user_input
from app.llm.factory import create_chat_model


WorkflowIntent = Literal[
    "knowledge_query",
    "calculation",
    "education_recommend",
    "infusion_adjust",
    "general",
]


class TurnPlan(BaseModel):
    intent: WorkflowIntent = "general"
    action: InputAction = InputAction.ROUTE
    allow_rag: bool = False
    allow_side_effects: bool = False
    risk_level: Literal["none", "low", "high"] = "none"
    requires_approval: bool = False
    needs_user_input: bool = False
    effective_message: str
    direct_response: str | None = None
    confidence: float = 1.0
    resumed_from: str | None = None
    clarified_from: str | None = None


class TurnResult(BaseModel):
    output: str
    intent: WorkflowIntent = "general"
    status: Literal["completed", "needs_user_input", "interrupted", "cancelled"] = "completed"
    sources: list[dict] = Field(default_factory=list)
    intermediate_steps: list = Field(default_factory=list)


async def _classify_workflow_intent(message: str) -> WorkflowIntent:
    llm = create_chat_model()
    prompt = f"""判断用户当前请求的执行类型，只回复一个标签：

knowledge_query - 查询知识、文档、医学概念或规范
calculation - 数学计算
education_recommend - 明确要求为患者推荐宣教内容
infusion_adjust - 明确要求改变输液滴速
general - 闲聊、普通问答或以上都不匹配

用户消息：{extract_user_input(message)}"""
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = response.content if isinstance(response.content, str) else str(response.content)
    normalized = content.strip().lower()
    for intent in (
        "knowledge_query",
        "calculation",
        "education_recommend",
        "infusion_adjust",
        "general",
    ):
        if intent in normalized:
            return intent
    return "general"


async def plan_turn(
    message: str,
    conversation_state: dict | None = None,
) -> TurnPlan:
    classified = classify_user_input(message, conversation_state)
    effective_message = classified.effective_message or message
    direct_response = (
        resolve_intent_response(classified.intent, extract_user_input(message))
        if classified.action != InputAction.ROUTE
        else None
    )

    if direct_response is not None:
        high_risk = classified.intent == InputIntent.HIGH_RISK_CLINICAL_ACTION
        return TurnPlan(
            action=classified.action,
            allow_rag=False,
            allow_side_effects=False,
            risk_level="high" if high_risk else "none",
            requires_approval=high_risk,
            needs_user_input=classified.action == InputAction.CLARIFY,
            effective_message=effective_message,
            direct_response=direct_response,
            confidence=classified.confidence,
            resumed_from=classified.resumed_from,
            clarified_from=classified.clarified_from,
        )

    if classified.intent == InputIntent.GENERAL and not classified.allow_rag:
        return TurnPlan(
            intent="general",
            action=classified.action,
            allow_rag=False,
            effective_message=effective_message,
            confidence=classified.confidence,
        )

    if classified.action in (InputAction.RESUME, InputAction.ROUTE):
        intent = await _classify_workflow_intent(effective_message)
    else:
        intent = "general"

    if intent == "infusion_adjust":
        return TurnPlan(
            intent="infusion_adjust",
            action=InputAction.CLARIFY,
            allow_rag=False,
            allow_side_effects=False,
            risk_level="high",
            requires_approval=True,
            needs_user_input=True,
            effective_message=effective_message,
            direct_response=resolve_intent_response(InputIntent.HIGH_RISK_CLINICAL_ACTION, message),
            confidence=classified.confidence,
        )

    return TurnPlan(
        intent=intent,
        action=classified.action,
        allow_rag=intent == "knowledge_query",
        allow_side_effects=intent == "education_recommend",
        risk_level="low" if intent == "education_recommend" else "none",
        requires_approval=False,
        effective_message=effective_message,
        confidence=classified.confidence,
        resumed_from=classified.resumed_from,
        clarified_from=classified.clarified_from,
    )
