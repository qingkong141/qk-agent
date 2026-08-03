"""宣教场景 Agent Tools"""

from langchain_core.tools import tool


def _get_agent():
    from app.graph.agents.education import EducationAgent
    return EducationAgent()


@tool
async def recommend_education(diagnosis_text: str, patient_id: str = "") -> str:
    """分析诊断文本，推荐宣教内容。返回推荐列表和 decision_id 供用户确认。

    Args:
        diagnosis_text: 诊断文本，如"2型糖尿病，HbA1c 8.5%"
        patient_id: 患者ID（可选）
    """
    analyzed = await _get_agent().analyze(diagnosis_text, patient_id)
    items = analyzed.get("items", [])
    if not items:
        return "未能从诊断文本中分析出需要推荐的宣教内容。"

    # 创建决策（通过 Agent 的完整 run 流程）
    from app.services.clinical_decision_service import create_decision, compute_status

    action_params = {"items": items, **{k: v for k, v in analyzed.items() if k != "items"}}

    decision = await create_decision(
        action_type="education_push",
        patient_id=patient_id,
        trigger_source="chat",
        trigger_text=diagnosis_text,
        action_params=action_params,
        extracted_info=analyzed.get("extracted_info"),
        safety_level="low",
    )

    lines = [f"📋 宣教推荐 (decision_id: {decision.id})"]
    for item in items[:5]:
        score_pct = int(item.get("score", 0) * 100)
        reason = item.get("reason", "")
        lines.append(f"  [{item['id']}] {item['title']}（匹配度 {score_pct}%）")
        if reason:
            lines.append(f"      理由：{reason}")
    lines.append(f"\n共 {len(items)} 条推荐，请回复要推送的内容编号，如'确认推送第1,2项'。")
    return "\n".join(lines)


@tool
async def confirm_education_push(decision_id: str, selected_ids: str) -> str:
    """确认推送宣教内容。支持部分确认。

    Args:
        decision_id: 宣教推荐返回的 decision_id
        selected_ids: 要推送的宣教ID，逗号分隔，如 "K001,K002"
    """
    from app.services.clinical_decision_service import confirm_decision, get_decision

    ids = [s.strip() for s in selected_ids.split(",") if s.strip()]
    if not ids:
        return "未指定要推送的宣教内容。"

    decision = await get_decision(decision_id)
    if not decision:
        return f"未找到决策记录 {decision_id}"

    result = await confirm_decision(decision_id, ids)

    # TODO: 对接宣教系统 POST /education/push
    # await education_api.push(patient_id=decision.patient_id, education_ids=ids)

    return result["summary"]


@tool
async def skip_education_items(decision_id: str, skip_ids: str) -> str:
    """跳过不需要推送的宣教条目。

    Args:
        decision_id: 宣教推荐返回的 decision_id
        skip_ids: 要跳过的宣教ID，逗号分隔
    """
    from app.services.clinical_decision_service import skip_items

    ids = [s.strip() for s in skip_ids.split(",") if s.strip()]
    if not ids:
        return "未指定要跳过的条目。"

    result = await skip_items(decision_id, ids)
    return result["summary"]
