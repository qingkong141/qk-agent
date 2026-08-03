"""从已保存的决策事实生成审计报告，不生成新的临床判断。"""

from __future__ import annotations

from typing import Any

from app.models.clinical_decision import ClinicalDecision


def build_decision_report(decision: ClinicalDecision) -> dict[str, Any]:
    params = decision.action_params or {}
    items = params.get("items", [])
    counts: dict[str, int] = {}
    for item in items:
        status = item.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1

    return {
        "report_type": "clinical_decision_audit",
        "decision_id": decision.id,
        "patient_id": decision.patient_id,
        "action_type": decision.action_type,
        "trigger": {
            "source": decision.trigger_source,
            "text": decision.trigger_text,
            "created_at": decision.created_at,
        },
        "extracted_facts": decision.extracted_info or {},
        "risk": {
            "level": decision.safety_level,
            "assessment": params.get("risk_assessment", {}),
        },
        "decision": {
            "status": decision.status,
            "items": items,
            "item_counts": counts,
        },
        "confirmation": {
            "confirmed_by": decision.confirmed_by,
            "last_confirmed_at": decision.last_confirmed_at,
        },
        "disclaimer": "本报告仅汇总系统中已记录的事实、建议和操作，不构成新的诊断或治疗意见。",
    }
