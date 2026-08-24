"""临床事件 API — 接收 HIS/EMR/监护仪等外部系统的临床事件"""

from fastapi import APIRouter, Depends

from app.clinical.context_extractor import extract_clinical_context
from app.dependencies import CurrentUser
from app.core.context import set_request_context
from app.schemas.clinical import ClinicalEventRequest, ClinicalEventResponse
from app.services.clinical_decision_service import create_decision
from app.services.conversation_access import conversation_external_user_id

router = APIRouter(prefix="/clinical-events", tags=["clinical-events"])


def _get_event_agent(event_type: str):
    """延迟导入，避免循环依赖"""
    from app.graph.agents.education import EducationAgent
    from app.graph.agents.infusion import InfusionAgent

    mapping = {
        "diagnosis_created": EducationAgent,
        "diagnosis_updated": EducationAgent,
        "vital_alert": InfusionAgent,
        "lab_result": EducationAgent,
        "prescription_new": EducationAgent,
    }
    agent_cls = mapping.get(event_type)
    return agent_cls() if agent_cls else None


@router.post("", response_model=ClinicalEventResponse, status_code=202)
async def receive_clinical_event(
    data: ClinicalEventRequest,
    user: CurrentUser,
):
    """
    接收外部临床事件，触发 AI 分析并生成决策。

    支持的事件类型：
    - diagnosis_created / diagnosis_updated: 诊断事件 → 宣教推荐
    - vital_alert: 体征告警 → 输液调整
    - lab_result: 检验结果 → 宣教推荐
    - prescription_new: 新处方 → 用药指导
    """
    set_request_context(
        user["id"], user.get("workspace", "default"),
        external_user_id=conversation_external_user_id(user),
        conversation_id=data.conversation_id or "",
    )
    idempotency_key = (data.metadata or {}).get("idempotency_key")
    agent = _get_event_agent(data.event_type)

    if agent is None:
        # 未知事件类型，仅记录不处理
        from app.services.clinical_decision_service import compute_status

        decision = await create_decision(
            action_type="unknown",
            patient_id=data.patient_id,
            patient_name=data.patient_name,
            trigger_source="his_webhook",
            trigger_text=str(data.payload),
            action_params={"items": [], "event_type": data.event_type},
            safety_level="low",
            conversation_id=data.conversation_id,
            idempotency_key=idempotency_key,
        )
        return ClinicalEventResponse(
            decision_id=decision.id,
            action_type="unknown",
            status=decision.status,
            injected_to_conversation=False,
        )

    # 触发 Agent 分析
    trigger_text = str(data.payload)
    clinical_context = await extract_clinical_context(trigger_text, data.patient_id)
    analyzed = await agent.analyze(
        trigger_text,
        data.patient_id,
        clinical_context=clinical_context.model_dump(),
    )
    analyzed.setdefault("extracted_info", clinical_context.model_dump(exclude={"source_text"}))
    safety = await agent.safety_check(analyzed)

    items = analyzed.get("items", [])
    action_params = {
        "items": items,
        **{k: v for k, v in analyzed.items() if k != "items"},
        "risk_assessment": safety,
    }

    decision = await create_decision(
        action_type=agent.action_type,
        patient_id=data.patient_id,
        patient_name=data.patient_name,
        trigger_source="his_webhook",
        trigger_text=trigger_text,
        action_params=action_params,
        extracted_info=analyzed.get("extracted_info"),
        safety_level=safety["level"],
        conversation_id=data.conversation_id,
        idempotency_key=idempotency_key,
    )

    # TODO: 如果有活跃的 conversation_id，注入系统消息到对话
    # if data.conversation_id:
    #     await inject_system_message(data.conversation_id, decision)

    return ClinicalEventResponse(
        decision_id=decision.id,
        action_type=agent.action_type,
        status=decision.status,
        injected_to_conversation=False,
    )
