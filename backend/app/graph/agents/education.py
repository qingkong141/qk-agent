"""宣教推送场景 Agent"""

from __future__ import annotations

import json
import os

from langchain_core.messages import HumanMessage

from app.graph.agents.base import ClinicalAgent
from app.llm.factory import create_chat_model

EDUCATION_SYSTEM_PROMPT = """你是一个临床宣教推荐助手。根据患者的诊断信息，推荐合适的宣教内容。

分析步骤：
1. 从用户输入中提取诊断信息、科室、关键指标
2. 生成宣教检索关键词（如"糖尿病饮食管理"、"血糖控制"等）
3. 输出 JSON 格式结果

输出格式（严格 JSON，不要额外文字）：
{
  "diagnosis": ["诊断1", "诊断2"],
  "department": "科室",
  "indicators": {"指标名": "值"},
  "keywords": ["检索关键词1", "关键词2", ...],
  "items": [
    {
      "id": "K001",
      "title": "宣教标题",
      "score": 0.95,
      "reason": "匹配此宣教内容的理由"
    }
  ]
}"""


class EducationAgent(ClinicalAgent):
    """宣教推送 Agent — 低风险，轻量分析，可用小模型"""

    action_type = "education_push"
    safety_level = "low"

    def __init__(self):
        from app.config import settings
        self.model_name = settings.EDUCATION_AGENT_MODEL or None  # 空 = 全局默认
        self.enable_reflection = True

    system_prompt = EDUCATION_SYSTEM_PROMPT

    async def analyze(self, text: str, patient_id: str, **context) -> dict:
        """LLM 提取诊断信息 + 生成检索关键词 + 排序推荐"""
        llm = create_chat_model()
        clinical_context = context.get("clinical_context", {})
        messages = [HumanMessage(content=(
            f"{self.system_prompt}\n\n诊断文本：{text}\n\n"
            f"已结构化临床上下文：{json.dumps(clinical_context, ensure_ascii=False)}"
        ))]
        response = await llm.ainvoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)

        try:
            # 尝试解析 JSON
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(content)
        except json.JSONDecodeError:
            result = {
                "diagnosis": [],
                "department": "",
                "keywords": [],
                "items": [],
                "parse_error": content,
            }

        # TODO: 调用宣教系统 GET /education/search 填充 items
        # for kw in result.get("keywords", []):
        #     edu_items = await education_api.search(keyword=kw)
        #     items.extend(edu_items)
        # result["items"] = rank_and_deduplicate(items)

        if not result.get("items"):
            keywords = result.get("keywords", [])
            result["items"] = [
                {
                    "id": f"K{i + 1:03d}",
                    "title": kw,
                    "score": 0.9 - i * 0.05,
                    "reason": f"诊断匹配关键词：{kw}",
                }
                for i, kw in enumerate(keywords[:5])
            ]

        result["extracted_info"] = {
            **clinical_context,
            "diagnosis": result.get("diagnosis", []),
            "indicators": result.get("indicators", {}),
            "department": result.get("department", ""),
        }

        return result

    async def execute(self, decision_id: str, selected_ids: list[str]) -> dict:
        """调用宣教系统推送 API"""
        # TODO: 对接宣教系统 POST /education/push
        # response = await education_api.push(
        #     patient_id=...,
        #     education_ids=selected_ids,
        #     source="ai_recommend",
        #     decision_id=decision_id,
        # )
        return {"success": True, "pushed": selected_ids, "note": "TODO: 对接宣教系统推送接口"}
