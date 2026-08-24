from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.clinical.risk_engine import is_execution_blocked
from app.core.context import get_request_context
from app.db.session import async_session
from app.models.clinical_decision import ClinicalDecision

STATUS_EXECUTED = "executed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
FINAL_STATUSES = frozenset({STATUS_EXECUTED, STATUS_SKIPPED, STATUS_FAILED})


def compute_status(items: list[dict]) -> str:
    if not items:
        return "pending"
    statuses = {item.get("status", STATUS_PENDING) for item in items}
    if all(status in FINAL_STATUSES for status in statuses):
        return "resolved"
    if any(status in FINAL_STATUSES for status in statuses):
        return "partial"
    return "pending"


async def create_decision(
    *, action_type: str, patient_id: str, trigger_source: str, trigger_text: str,
    action_params: dict[str, Any], patient_name: str = "",
    extracted_info: dict[str, Any] | None = None, safety_level: str = "low",
    conversation_id: str | None = None, idempotency_key: str | None = None,
) -> ClinicalDecision:
    ctx = get_request_context()
    owner_id = ctx.get("user_id", "")
    external_user_id = ctx.get("external_user_id", "")
    key = idempotency_key or str(uuid.uuid4())
    async with async_session() as db:
        existing = await db.scalar(select(ClinicalDecision).where(
            ClinicalDecision.owner_id == owner_id,
            ClinicalDecision.external_user_id == external_user_id,
            ClinicalDecision.idempotency_key == key,
        ))
        if existing:
            return existing
        decision = ClinicalDecision(
            id=str(uuid.uuid4()), owner_id=owner_id, external_user_id=external_user_id,
            idempotency_key=key, conversation_id=conversation_id, patient_id=patient_id,
            patient_name=patient_name, action_type=action_type, trigger_source=trigger_source,
            trigger_text=trigger_text, extracted_info=extracted_info,
            action_params=action_params, safety_level=safety_level, status="pending",
        )
        db.add(decision)
        await db.commit()
        await db.refresh(decision)
        return decision


def _owned(stmt, owner_id: str | None, external_user_id: str | None):
    if owner_id is not None:
        stmt = stmt.where(ClinicalDecision.owner_id == owner_id)
    if external_user_id is not None:
        stmt = stmt.where(ClinicalDecision.external_user_id == external_user_id)
    return stmt


async def get_decision(decision_id: str, owner_id: str | None = None,
                       external_user_id: str | None = None) -> ClinicalDecision | None:
    async with async_session() as db:
        return await db.scalar(_owned(
            select(ClinicalDecision).where(ClinicalDecision.id == decision_id),
            owner_id, external_user_id,
        ))


async def list_decisions(*, action_type: str | None = None, status: str | None = None,
                         patient_id: str | None = None, limit: int = 50, offset: int = 0,
                         owner_id: str | None = None,
                         external_user_id: str | None = None) -> list[ClinicalDecision]:
    async with async_session() as db:
        stmt = _owned(select(ClinicalDecision), owner_id, external_user_id)
        if action_type:
            stmt = stmt.where(ClinicalDecision.action_type == action_type)
        if status:
            stmt = stmt.where(ClinicalDecision.status.in_([s.strip() for s in status.split(",")]))
        if patient_id:
            stmt = stmt.where(ClinicalDecision.patient_id == patient_id)
        result = await db.execute(stmt.order_by(ClinicalDecision.created_at.desc()).offset(offset).limit(limit))
        return list(result.scalars().all())


async def confirm_decision(
    decision_id: str, selected_ids: list[str], *, execute_fn=None,
    confirmed_by: str | None = None, owner_id: str | None = None,
    external_user_id: str | None = None,
) -> dict[str, Any]:
    async with async_session() as db:
        decision = await db.scalar(_owned(
            select(ClinicalDecision).where(ClinicalDecision.id == decision_id),
            owner_id, external_user_id,
        ))
        if not decision:
            raise ValueError(f"Decision {decision_id} does not exist")
        if decision.status not in ("pending", "partial"):
            raise ValueError(f"Decision {decision_id} cannot be confirmed from {decision.status}")
        if is_execution_blocked(decision.action_params):
            raise ValueError(f"Decision {decision_id} is blocked by safety policy")

        claimed = await db.execute(update(ClinicalDecision).where(
            ClinicalDecision.id == decision.id,
            ClinicalDecision.version == decision.version,
            ClinicalDecision.status.in_(("pending", "partial")),
        ).values(status="executing", version=decision.version + 1))
        if claimed.rowcount != 1:
            await db.rollback()
            raise ValueError(f"Decision {decision_id} is already executing or completed")
        await db.commit()

        items = list(decision.action_params.get("items", []))
        selected = set(selected_ids)
        executed, failed = [], []
        for item in items:
            if item["id"] not in selected or item.get("status", STATUS_PENDING) != STATUS_PENDING:
                continue
            try:
                if execute_fn:
                    result = execute_fn(item, decision)
                    if inspect.isawaitable(result):
                        await result
                item["status"] = STATUS_EXECUTED
                executed.append({"id": item["id"], "title": item.get("title", ""), "status": "success"})
            except Exception as exc:
                item["status"] = STATUS_FAILED
                item["error"] = str(exc)
                failed.append({"id": item["id"], "title": item.get("title", ""), "status": "failed", "error": str(exc)})

        decision = await db.get(ClinicalDecision, decision_id)
        decision.action_params = {**decision.action_params, "items": items}
        decision.status = compute_status(items)
        decision.confirmed_by = confirmed_by
        decision.last_confirmed_at = datetime.now(timezone.utc)
        decision.version += 1
        await db.commit()
        remaining = [item for item in items if item.get("status", STATUS_PENDING) == STATUS_PENDING]
        summary = f"Executed {len(executed)} item(s)"
        if remaining:
            summary += f", {len(remaining)} remaining"
        return {"decision_id": decision.id, "action_type": decision.action_type,
                "status": decision.status, "executed": executed, "failed": failed,
                "skipped": [], "remaining": remaining, "summary": summary}


async def skip_items(decision_id: str, skip_ids: list[str], *, owner_id: str | None = None,
                     external_user_id: str | None = None) -> dict[str, Any]:
    async with async_session() as db:
        decision = await db.scalar(_owned(select(ClinicalDecision).where(
            ClinicalDecision.id == decision_id), owner_id, external_user_id))
        if not decision or decision.status not in ("pending", "partial"):
            raise ValueError("Decision does not exist or cannot be changed")
        skip_set = set(skip_ids)
        items = list(decision.action_params.get("items", []))
        skipped = []
        for item in items:
            if item["id"] in skip_set and item.get("status", STATUS_PENDING) == STATUS_PENDING:
                item["status"] = STATUS_SKIPPED
                skipped.append(item["id"])
        decision.action_params = {**decision.action_params, "items": items}
        decision.status = compute_status(items)
        decision.version += 1
        await db.commit()
        remaining = [item for item in items if item.get("status", STATUS_PENDING) == STATUS_PENDING]
        return {"decision_id": decision.id, "action_type": decision.action_type,
                "status": decision.status, "executed": [], "failed": [],
                "skipped": skipped, "remaining": remaining, "summary": f"Skipped {len(skipped)} item(s)"}


async def cancel_decision(decision_id: str, *, owner_id: str | None = None,
                          external_user_id: str | None = None) -> dict[str, Any]:
    decision = await get_decision(decision_id, owner_id, external_user_id)
    if not decision:
        raise ValueError("Decision does not exist")
    pending = [item["id"] for item in decision.action_params.get("items", [])
               if item.get("status", STATUS_PENDING) == STATUS_PENDING]
    result = await skip_items(decision_id, pending, owner_id=owner_id,
                              external_user_id=external_user_id)
    result["summary"] = "Decision cancelled"
    return result
