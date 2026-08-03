"""临床决策服务 — 通用决策编排逻辑"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.db.session import async_session
from app.models.clinical_decision import ClinicalDecision
from app.clinical.risk_engine import is_execution_blocked

STATUS_EXECUTED = "executed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"

FINAL_STATUSES = frozenset({STATUS_EXECUTED, STATUS_SKIPPED, STATUS_FAILED})


def compute_status(items: list[dict]) -> str:
    """聚合条目状态得出整体 status"""
    if not items:
        return "pending"
    statuses = {item.get("status", STATUS_PENDING) for item in items}
    if all(s in FINAL_STATUSES for s in statuses):
        return "resolved"
    if any(s == STATUS_PENDING for s in statuses):
        if any(s in FINAL_STATUSES for s in statuses):
            return "partial"
        return "pending"
    return "resolved"


async def create_decision(
    *,
    action_type: str,
    patient_id: str,
    patient_name: str = "",
    trigger_source: str,
    trigger_text: str,
    action_params: dict[str, Any],
    extracted_info: dict[str, Any] | None = None,
    safety_level: str = "low",
    conversation_id: str | None = None,
) -> ClinicalDecision:
    """创建决策记录"""
    decision_id = str(uuid.uuid4())
    decision = ClinicalDecision(
        id=decision_id,
        conversation_id=conversation_id,
        patient_id=patient_id,
        patient_name=patient_name,
        action_type=action_type,
        trigger_source=trigger_source,
        trigger_text=trigger_text,
        extracted_info=extracted_info,
        action_params=action_params,
        safety_level=safety_level,
        status="pending",
    )
    async with async_session() as db:
        db.add(decision)
        await db.commit()
        await db.refresh(decision)
    return decision


async def get_decision(decision_id: str) -> ClinicalDecision | None:
    """查询决策"""
    async with async_session() as db:
        result = await db.execute(
            select(ClinicalDecision).where(ClinicalDecision.id == decision_id)
        )
        return result.scalar_one_or_none()


async def list_decisions(
    *,
    action_type: str | None = None,
    status: str | None = None,
    patient_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ClinicalDecision]:
    """查询决策列表"""
    async with async_session() as db:
        stmt = select(ClinicalDecision).order_by(ClinicalDecision.created_at.desc())
        if action_type:
            stmt = stmt.where(ClinicalDecision.action_type == action_type)
        if status:
            statuses = [s.strip() for s in status.split(",")]
            stmt = stmt.where(ClinicalDecision.status.in_(statuses))
        if patient_id:
            stmt = stmt.where(ClinicalDecision.patient_id == patient_id)
        stmt = stmt.offset(offset).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())


async def confirm_decision(
    decision_id: str,
    selected_ids: list[str],
    *,
    execute_fn=None,
    confirmed_by: str | None = None,
) -> dict[str, Any]:
    """通用确认逻辑 — 逐条执行 selected_ids，调用 execute_fn"""
    async with async_session() as db:
        result = await db.execute(
            select(ClinicalDecision).where(ClinicalDecision.id == decision_id)
        )
        decision = result.scalar_one_or_none()
        if not decision:
            raise ValueError(f"决策 {decision_id} 不存在")
        if decision.status not in ("pending", "partial"):
            raise ValueError(f"决策 {decision_id} 状态为 {decision.status}，不可确认")
        if is_execution_blocked(decision.action_params):
            raise ValueError(f"决策 {decision_id} 被安全规则阻断，不可执行")

        items: list[dict] = decision.action_params.get("items", [])
        selected = set(selected_ids)
        executed, failed = [], []

        for item in items:
            if item["id"] not in selected:
                continue
            try:
                if execute_fn:
                    execute_fn(item, decision)
                item["status"] = STATUS_EXECUTED
                executed.append({"id": item["id"], "title": item.get("title", ""), "status": "success"})
            except Exception as e:
                item["status"] = STATUS_FAILED
                item["error"] = str(e)
                failed.append({"id": item["id"], "title": item.get("title", ""), "status": "failed", "error": str(e)})

        decision.action_params["items"] = items
        decision.status = compute_status(items)
        decision.confirmed_by = confirmed_by
        # TODO: 从当前时间设置 last_confirmed_at
        from datetime import datetime

        decision.last_confirmed_at = datetime.utcnow()
        await db.commit()

        remaining = [item for item in items if item.get("status") == STATUS_PENDING]
        executed_count = len(executed)
        remaining_count = len(remaining)
        summary = f"已执行 {executed_count} 条"
        if remaining_count:
            summary += f"，还有 {remaining_count} 条待处理"

        return {
            "decision_id": decision.id,
            "action_type": decision.action_type,
            "status": decision.status,
            "executed": executed,
            "failed": failed,
            "skipped": [],
            "remaining": remaining,
            "summary": summary,
        }


async def skip_items(decision_id: str, skip_ids: list[str]) -> dict[str, Any]:
    """跳过指定条目"""
    async with async_session() as db:
        result = await db.execute(
            select(ClinicalDecision).where(ClinicalDecision.id == decision_id)
        )
        decision = result.scalar_one_or_none()
        if not decision:
            raise ValueError(f"决策 {decision_id} 不存在")

        items: list[dict] = decision.action_params.get("items", [])
        skip_set = set(skip_ids)
        skipped = []
        for item in items:
            if item["id"] in skip_set:
                item["status"] = STATUS_SKIPPED
                skipped.append(item["id"])

        decision.action_params["items"] = items
        decision.status = compute_status(items)
        await db.commit()

        remaining = [item for item in items if item.get("status") == STATUS_PENDING]
        return {
            "decision_id": decision.id,
            "action_type": decision.action_type,
            "status": decision.status,
            "executed": [],
            "failed": [],
            "skipped": skipped,
            "remaining": remaining,
            "summary": f"已跳过 {len(skipped)} 条",
        }


async def cancel_decision(decision_id: str) -> dict[str, Any]:
    """取消决策 — 所有 pending 条目标记为 skipped"""
    async with async_session() as db:
        result = await db.execute(
            select(ClinicalDecision).where(ClinicalDecision.id == decision_id)
        )
        decision = result.scalar_one_or_none()
        if not decision:
            raise ValueError(f"决策 {decision_id} 不存在")

        items: list[dict] = decision.action_params.get("items", [])
        for item in items:
            if item.get("status") == STATUS_PENDING:
                item["status"] = STATUS_SKIPPED

        decision.action_params["items"] = items
        decision.status = "resolved"
        await db.commit()

        return {
            "decision_id": decision.id,
            "action_type": decision.action_type,
            "status": decision.status,
            "executed": [],
            "failed": [],
            "skipped": [item["id"] for item in items if item["status"] == STATUS_SKIPPED],
            "remaining": [],
            "summary": "决策已取消",
        }
