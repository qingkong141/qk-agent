"""临床决策 API — 查询、确认、跳过、取消"""

from fastapi import APIRouter, Depends, HTTPException

from app.clinical.risk_engine import is_execution_blocked
from app.dependencies import CurrentUser, DbSession
from app.schemas.clinical import (
    ConfirmRequest,
    ClinicalDecisionReport,
    DecisionActionResponse,
    DecisionListItem,
    DecisionListResponse,
    SkipRequest,
)
from app.services.clinical_report_service import build_decision_report
from app.services.clinical_decision_service import (
    cancel_decision,
    confirm_decision,
    get_decision,
    list_decisions,
    skip_items,
)
from app.services.conversation_access import conversation_external_user_id

router = APIRouter(prefix="/clinical/decisions", tags=["clinical-decisions"])


@router.get("/{decision_id}/report", response_model=ClinicalDecisionReport)
async def get_clinical_decision_report(
    decision_id: str,
    db: DbSession,
    user: CurrentUser,
):
    """返回只包含已记录事实的临床决策审计报告。"""
    decision = await get_decision(decision_id, user["id"], conversation_external_user_id(user))
    if not decision:
        raise HTTPException(status_code=404, detail="决策不存在")
    return ClinicalDecisionReport(**build_decision_report(decision))


@router.get("", response_model=DecisionListResponse)
async def list_clinical_decisions(
    db: DbSession,
    user: CurrentUser,
    action_type: str | None = None,
    status: str | None = None,
    patient_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """查询临床决策列表"""
    items = await list_decisions(
        action_type=action_type,
        status=status,
        patient_id=patient_id,
        limit=limit,
        offset=offset,
        owner_id=user["id"],
        external_user_id=conversation_external_user_id(user),
    )
    return DecisionListResponse(
        items=[
            DecisionListItem(
                id=item.id,
                action_type=item.action_type,
                patient_name=item.patient_name,
                trigger_text=item.trigger_text,
                action_params=item.action_params or {},
                safety_level=item.safety_level or "low",
                status=item.status,
                created_at=item.created_at,
            )
            for item in items
        ]
    )


@router.post("/{decision_id}/confirm", response_model=DecisionActionResponse)
async def confirm_clinical_decision(
    decision_id: str,
    data: ConfirmRequest,
    db: DbSession,
    user: CurrentUser,
):
    """确认并执行决策中的指定条目。支持部分确认。"""
    decision = await get_decision(decision_id, user["id"], conversation_external_user_id(user))
    if not decision:
        raise HTTPException(status_code=404, detail="决策不存在")
    if decision.status not in ("pending", "partial"):
        raise HTTPException(status_code=400, detail=f"决策状态为 {decision.status}，不可确认")
    if is_execution_blocked(decision.action_params):
        raise HTTPException(status_code=409, detail="该决策被临床安全规则阻断，不可执行")

    # 1. 更新决策状态
    result = await confirm_decision(
        decision_id,
        data.selected_ids,
        confirmed_by=user["id"],
        owner_id=user["id"],
        external_user_id=conversation_external_user_id(user),
    )

    # 2. 根据 action_type 调用对应业务系统的执行接口
    if decision.action_type == "education_push":
        # TODO: 对接宣教系统 POST /education/push
        # await education_api.push(patient_id=decision.patient_id, education_ids=data.selected_ids, ...)
        pass
    elif decision.action_type == "infusion_adjust":
        # TODO: 对接输液泵系统 POST /infusion/device/{device_id}/rate
        pass

    return DecisionActionResponse(**result)


@router.post("/{decision_id}/skip", response_model=DecisionActionResponse)
async def skip_clinical_decision_items(
    decision_id: str,
    data: SkipRequest,
    db: DbSession,
    user: CurrentUser,
):
    """跳过决策中的指定条目"""
    decision = await get_decision(decision_id, user["id"], conversation_external_user_id(user))
    if not decision:
        raise HTTPException(status_code=404, detail="决策不存在")
    if decision.status not in ("pending", "partial"):
        raise HTTPException(status_code=400, detail=f"决策状态为 {decision.status}，不可操作")

    result = await skip_items(
        decision_id, data.skip_ids, owner_id=user["id"],
        external_user_id=conversation_external_user_id(user),
    )
    return DecisionActionResponse(**result)


@router.post("/{decision_id}/cancel", response_model=DecisionActionResponse)
async def cancel_clinical_decision(
    decision_id: str,
    db: DbSession,
    user: CurrentUser,
):
    """取消整个决策（所有 pending 条目标记为 skipped）"""
    decision = await get_decision(decision_id, user["id"], conversation_external_user_id(user))
    if not decision:
        raise HTTPException(status_code=404, detail="决策不存在")
    if decision.status not in ("pending", "partial"):
        raise HTTPException(status_code=400, detail=f"决策状态为 {decision.status}，不可取消")

    result = await cancel_decision(
        decision_id, owner_id=user["id"],
        external_user_id=conversation_external_user_id(user),
    )
    return DecisionActionResponse(**result)
