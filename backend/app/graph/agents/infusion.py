"""输液滴速调整场景 Agent"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

from app.clinical.risk_engine import assess_infusion_risk
from app.graph.agents.base import ClinicalAgent
from app.llm.factory import create_chat_model

INFUSION_SYSTEM_PROMPT = """你是一个输液管理助手。根据患者的体征或用户指令，建议输液滴速调整。

分析步骤：
1. 从用户输入中提取当前滴速、目标滴速、患者床号、调整原因
2. 评估安全性：调幅超过 30% 需标记 safety_level 为 medium
3. 输出 JSON 格式结果

输出格式（严格 JSON，不要额外文字）：
{
  "device_id": "设备ID",
  "patient_bed": "床号",
  "current_rate": 60,
  "suggested_rate": 40,
  "reason": "调整原因",
  "safety_level": "medium",
  "warnings": ["心率从75升至120，建议降低滴速"],
  "items": [
    {
      "id": "rate_adjust",
      "action": "adjust_rate",
      "from": 60,
      "to": 40,
      "reason": "心率升高，降低滴速以减轻心脏负荷"
    }
  ]
}"""


class InfusionAgent(ClinicalAgent):
    """输液调整 Agent — 中高风险，涉及患者安全，建议用强模型"""

    action_type = "infusion_adjust"
    safety_level = "medium"

    def __init__(self):
        from app.config import settings
        self.model_name = settings.INFUSION_AGENT_MODEL or None  # 空 = 全局默认
        self.enable_reflection = True

    system_prompt = INFUSION_SYSTEM_PROMPT

    async def analyze(self, text: str, patient_id: str, **context) -> dict:
        """LLM 解析输液调整指令"""
        llm = create_chat_model()
        clinical_context = context.get("clinical_context", {})
        messages = [HumanMessage(content=(
            f"{self.system_prompt}\n\n用户输入：{text}\n\n"
            f"已结构化临床上下文：{json.dumps(clinical_context, ensure_ascii=False)}"
        ))]
        response = await llm.ainvoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)

        try:
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(content)
        except json.JSONDecodeError:
            result = {
                "device_id": "",
                "current_rate": 0,
                "suggested_rate": 0,
                "reason": "",
                "safety_level": "medium",
                "warnings": ["JSON 解析失败"],
                "items": [],
                "parse_error": content,
            }

        # 确保 items 存在
        if not result.get("items"):
            result["items"] = [
                {
                    "id": "rate_adjust",
                    "action": "adjust_rate",
                    "from": result.get("current_rate", 0),
                    "to": result.get("suggested_rate", 0),
                    "reason": result.get("reason", ""),
                }
            ]

        result["extracted_info"] = {
            **clinical_context,
            "device_id": result.get("device_id", ""),
            "current_rate": result.get("current_rate", 0),
            "vital_signs": {},
        }

        return result

    async def safety_check(self, params: dict) -> dict:
        """输液安全校验由确定性规则引擎完成。"""
        assessment = assess_infusion_risk(params)
        assessment["warnings"] = list(dict.fromkeys(params.get("warnings", []) + assessment["warnings"]))
        return assessment

    async def execute(self, decision_id: str, selected_ids: list[str]) -> dict:
        """调用输液泵 API 调整滴速"""
        # TODO: 对接输液泵系统 POST /infusion/device/{device_id}/rate
        # decision = await get_decision(decision_id)
        # device_id = decision.action_params.get("device_id")
        # target_rate = decision.action_params.get("suggested_rate")
        # response = await infusion_api.set_rate(device_id, target_rate)
        return {
            "success": True,
            "adjusted": selected_ids,
            "note": "TODO: 对接输液泵系统接口",
        }
