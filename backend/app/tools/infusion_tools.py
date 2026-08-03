"""输液场景 Agent Tools"""

from langchain_core.tools import tool


@tool
async def query_infusion_status(patient_id: str) -> str:
    """查询患者当前输液滴速和设备状态。

    Args:
        patient_id: 患者ID或床号
    """
    # TODO: 对接输液泵系统 GET /infusion/device/{device_id}/status
    return f"TODO: 对接输液泵系统 - 查询患者 {patient_id} 的输液状态"


@tool
async def adjust_infusion_rate(
    patient_id: str,
    target_rate: int,
    reason: str = "",
) -> str:
    """调整输液滴速。AI 会先做安全检查再建议确认。

    Args:
        patient_id: 患者ID或床号
        target_rate: 目标滴速（滴/分钟）
        reason: 调整原因
    """
    # TODO: 对接输液泵系统 - 安全检查 + 确认后调用 POST /infusion/device/{device_id}/rate
    safety_note = ""
    if target_rate <= 0:
        return "错误：目标滴速必须大于 0"
    if target_rate > 200:
        safety_note = "⚠️ 警告：目标滴速超过 200，可能超出安全范围，需医生确认"

    # 创建决策记录
    from app.services.clinical_decision_service import create_decision

    items = [
        {
            "id": "rate_adjust",
            "action": "adjust_rate",
            "from": 0,  # TODO: 从输液泵获取当前滴速
            "to": target_rate,
            "reason": reason or f"用户指令调整至 {target_rate}",
        }
    ]

    action_params = {
        "items": items,
        "patient_id": patient_id,
        "suggested_rate": target_rate,
        "reason": reason,
    }

    decision = await create_decision(
        action_type="infusion_adjust",
        patient_id=patient_id,
        trigger_source="chat",
        trigger_text=f"调整滴速至 {target_rate}",
        action_params=action_params,
        safety_level="medium",
    )

    msg = f"🩺 输液调整建议 (decision_id: {decision.id})\n"
    msg += f"  目标滴速：{target_rate} 滴/分钟\n"
    if reason:
        msg += f"  原因：{reason}\n"
    if safety_note:
        msg += f"\n{safety_note}\n"
    msg += "\n回复'确认'以执行调整。"
    return msg
