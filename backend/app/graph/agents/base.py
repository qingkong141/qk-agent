"""临床场景 Agent 基类

支持的能力矩阵：
    ReAct              ✅ AgentExecutor 层
    Plan-and-Execute   🔧 脚手架就绪，待 LLM 规划器接入
    Multi-Agent         ✅ model_name 按场景分层
    Reflective          ✅ self_review 自检
    Tool-Augmented      ✅ ToolManager 注册
    Memory-Augmented    ✅ save/recall Tool
    RAG                ✅ rag_pipeline
    Autonomous Loop    ❌ 医疗场景不加（max_iterations 是安全阀）
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage

from app.clinical.context_extractor import extract_clinical_context
from app.core.prompts import extract_user_input
from app.graph.state import WorkflowState
from app.llm.factory import create_chat_model
from app.services.clinical_decision_service import create_decision
from app.services.usage_tracker import estimate_tokens, log_usage


class ClinicalAgent:
    """临床场景 Agent 基类"""

    # ──── 子类必须覆盖 ────
    action_type: str = ""       # education_push | infusion_adjust | ...
    safety_level: str = "low"   # low | medium | high
    system_prompt: str = ""     # 场景特定的分析 prompt

    # ──── 子类可选覆盖 ────
    model_name: str | None = None  # None = 用全局默认；"Qwen3-4B" = 本 Agent 专用模型
    enable_reflection: bool = True  # 是否启用自检
    reflection_prompt: str = ""     # 自检 prompt（为空则用默认）

    # ──── Plan-and-Execute 脚手架 ────
    # 预留字段，当前为 False。设为 True 时 run() 将先让 LLM 出计划再执行
    use_planning: bool = False

    # ──── 默认自检 prompt ────
    DEFAULT_REFLECTION_PROMPT = """你是临床决策审查专家。请审查以下推荐结果，指出问题并修正。

审查维度：
1. 去重：是否有内容重复或高度相似的条目？合并或删除
2. 相关性：是否所有条目都与当前诊断/场景直接相关？不相关的降低评分或删除
3. 完整性：是否遗漏了重要的推荐项？如有，请补充
4. 安全性：是否有不适用于该患者的推荐？标记风险

输出格式（严格 JSON，不要额外文字）：
{
  "issues_found": true,
  "changes": [
    {"type": "merge|remove|add|adjust_score|warning", "item_id": "K001", "detail": "具体问题和处理"}
  ],
  "items": [
    {"id": "K001", "title": "修正后的标题", "score": 0.95, "reason": "修正后的理由"}
  ]
}

如果没有问题，issues_found 为 false，items 返回原样即可。
"""

    # ──── 核心方法 ────

    def _get_llm(self, streaming: bool = False):
        """获取 LLM 实例，支持按 Agent 指定模型"""
        return create_chat_model(model_name=self.model_name, streaming=streaming)

    async def analyze(self, text: str, patient_id: str, **context) -> dict:
        """LLM 分析提取 — 子类必须实现"""
        raise NotImplementedError

    async def execute(self, decision_id: str, selected_ids: list[str]) -> dict:
        """执行确认操作 — 子类必须实现"""
        raise NotImplementedError

    async def safety_check(self, params: dict) -> dict[str, Any]:
        """安全检查"""
        return {"level": self.safety_level, "warnings": []}

    # ──── 🆕 Reflective 自检 ────

    async def reflect(self, analyzed: dict) -> dict:
        """自检：对 analyze() 的结果做审查和修正"""
        if not self.enable_reflection:
            return analyzed

        items = analyzed.get("items", [])
        if not items:
            return analyzed

        reflection_prompt = self.reflection_prompt or self.DEFAULT_REFLECTION_PROMPT

        try:
            llm = self._get_llm()
            prompt = f"""{reflection_prompt}

原始推荐结果：
{json.dumps(items, ensure_ascii=False, indent=2)}

原始分析上下文：
{json.dumps(analyzed.get("extracted_info", {}), ensure_ascii=False, indent=2)}"""
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content if isinstance(response.content, str) else str(response.content)

            # 解析 JSON
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            review = json.loads(content)

            if review.get("issues_found"):
                analyzed["items"] = review.get("items", items)
                analyzed["reflection_changes"] = review.get("changes", [])
                analyzed["reflection_applied"] = True

                await log_usage(
                    "system", "agent_run",
                    agent_name=f"{self.action_type}_reflection",
                    tokens=estimate_tokens(content),
                )
        except Exception:
            # 自检失败不影响主流程
            analyzed["reflection_applied"] = False
            analyzed["reflection_error"] = "自检解析异常，使用原始结果"

        return analyzed

    # ──── 🆕 Plan-and-Execute 脚手架 ────

    async def plan(self, text: str, patient_id: str) -> list[dict]:
        """LLM 制定执行计划（use_planning=True 时调用）

        返回: [{"step": 1, "action": "extract_diagnosis", "description": "提取诊断信息"}, ...]
        """
        llm = self._get_llm()
        prompt = f"""你是一个临床任务规划器。请为以下临床任务制定分步执行计划。

任务类型：{self.action_type}
用户输入：{text}

生成 3-5 个执行步骤。输出格式（严格 JSON 数组）：
[
  {{"step": 1, "action": "动作标识", "description": "本步做什么"}}
]"""
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if isinstance(response.content, str) else str(response.content)
        try:
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(content)
        except json.JSONDecodeError:
            return [{"step": 1, "action": "analyze", "description": "直接分析"}]

    # ──── 🔧 主流程（扩展了 Reflective + Plan-and-Execute 预留） ────

    async def run(self, state: WorkflowState) -> dict:
        """完整流程：规划(可选) → 分析 → 自检 → 安全检查 → 落库 → 格式化"""
        user_input = extract_user_input(state["user_input"])
        patient_id = state.get("user_id", "unknown")
        patient_name = ""
        conversation_id = state.get("workspace", "")

        # Step 0: 规划（Plan-and-Execute，可选）
        plan_steps = []
        if self.use_planning:
            plan_steps = await self.plan(user_input, patient_id)

        # Step 1: 统一提取临床上下文，再交给场景 Agent 分析
        try:
            clinical_context = await extract_clinical_context(user_input, patient_id)
            analyzed = await self.analyze(
                user_input,
                patient_id,
                clinical_context=clinical_context.model_dump(),
            )
            analyzed.setdefault("extracted_info", clinical_context.model_dump(exclude={"source_text"}))
        except Exception:
            analyzed = {"error": "分析失败", "items": []}

        # Step 2: 🆕 自检（Reflective）
        analyzed = await self.reflect(analyzed)

        # Step 3: 安全检查
        safety = await self.safety_check(analyzed)

        # Step 4: 落库
        items = analyzed.get("items", [])
        action_params = {
            "items": items,
            **{k: v for k, v in analyzed.items() if k not in ("items",)},
            "risk_assessment": safety,
        }
        if plan_steps:
            action_params["plan"] = plan_steps

        decision = await create_decision(
            action_type=self.action_type,
            patient_id=patient_id,
            patient_name=patient_name,
            trigger_source="chat",
            trigger_text=user_input,
            action_params=action_params,
            extracted_info=analyzed.get("extracted_info"),
            safety_level=safety["level"],
            conversation_id=conversation_id,
        )

        await log_usage(
            state.get("user_id", "anonymous"),
            "agent_run",
            agent_name=f"{self.action_type}_agent",
            tokens=estimate_tokens(json.dumps(analyzed)),
        )

        # Step 5: 格式化
        formatted = self.format_response(analyzed, decision.id)
        return {
            "agent_result": formatted,
            "intermediate_steps": [(f"{self.action_type}_analysis", formatted)],
            "action_type": self.action_type,
            "decision_id": decision.id,
            "safety_level": safety["level"],
        }

    def format_response(self, analyzed: dict, decision_id: str) -> str:
        """格式化给 response_agent 的文本"""
        items = analyzed.get("items", [])
        if not items:
            return "未能分析出需要推荐的内容。"

        lines = [f"决策ID: {decision_id}"]
        if analyzed.get("reflection_applied"):
            lines.append("⚡ 已通过智能审查优化推荐结果")
            for change in analyzed.get("reflection_changes", []):
                lines.append(f"  - {change.get('detail', '')}")
            lines.append("")

        for item in items:
            title = item.get("title", "")
            reason = item.get("reason", "")
            score = item.get("score", 0)
            lines.append(f"- [{item['id']}] {title} (匹配度 {int(score * 100)}%)")
            if reason:
                lines.append(f"  理由: {reason}")
        return "\n".join(lines)
