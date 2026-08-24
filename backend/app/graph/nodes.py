from app.core.agent_factory import create_agent_executor
from app.core.prompts import build_rag_response_prompt, extract_user_input
from app.graph.agents.education import EducationAgent
from app.graph.agents.infusion import InfusionAgent
from app.graph.state import WorkflowState
from app.rag.pipeline import is_kb_miss, parse_knowledge_sources, rag_pipeline
from app.services.usage_tracker import estimate_tokens, log_usage
from app.tools.registry import tool_manager


class _ToolStep:
    def __init__(self, tool: str):
        self.tool = tool

NODE_TASKS = {
    "supervisor": "分析用户意图并路由",
    "kb_agent": "检索知识库",
    "calc_agent": "执行数学计算",
    "education_agent": "宣教推荐分析",
    "infusion_agent": "输液调整分析",
    "response_agent": "合成最终回复",
}


async def supervisor_node(state: WorkflowState) -> dict:
    """Expose the already validated turn plan to the graph router."""
    plan = state.get("turn_plan", {})
    return {
        "intent": plan.get("intent", "general"),
        "response_status": "needs_user_input" if plan.get("needs_user_input") else "completed",
    }


async def kb_agent_node(state: WorkflowState) -> dict:
    if not state.get("turn_plan", {}).get("allow_rag", False):
        return {"agent_result": "", "kb_hit": False, "sources": []}
    query = extract_user_input(state["user_input"])
    kb_text, kb_hit, _ = rag_pipeline.resolve_search(query)
    sources = parse_knowledge_sources(kb_text) if kb_hit else []
    await log_usage(
        state["user_id"],
        "agent_run",
        agent_name="kb_agent",
        tokens=estimate_tokens(kb_text),
    )
    return {
        "agent_result": kb_text,
        "kb_hit": kb_hit,
        "sources": sources,
        "intermediate_steps": [(_ToolStep("search_knowledge_base"), kb_text)],
    }


async def calc_agent_node(state: WorkflowState) -> dict:
    agent = create_agent_executor(
        role="计算助手",
        capabilities="使用计算器工具执行数学运算",
        tool_names=["calculator"],
    )
    result = await agent.ainvoke({
        "input": state["user_input"],
        "chat_history": state.get("chat_history", []),
    })
    output = result.get("output", "")
    await log_usage(state["user_id"], "agent_run", agent_name="calc_agent", tokens=estimate_tokens(output))
    return {
        "agent_result": output,
        "intermediate_steps": result.get("intermediate_steps", []),
    }


async def response_agent_node(state: WorkflowState) -> dict:
    intent = state.get("intent", "general")
    agent_result = state.get("agent_result", "")
    kb_hit = state.get("kb_hit", False)
    question = extract_user_input(state["user_input"])
    plan = state.get("turn_plan", {})
    if output := plan.get("direct_response"):
        await log_usage(state["user_id"], "agent_run", agent_name="response_agent", tokens=estimate_tokens(output))
        return {
            "final_output": output,
            "response_status": "needs_user_input" if plan.get("needs_user_input") else "completed",
            "kb_hit": False,
            "sources": [],
        }

    sources = (
        state.get("sources") or []
        if plan.get("allow_rag") and kb_hit
        else []
    )
    if intent != "calculation":
        kb_hit = kb_hit and not is_kb_miss(agent_result)

    prompt_parts = []
    if agent_prompt := state.get("agent_prompt", "").strip():
        prompt_parts.append(agent_prompt)
    prompt_parts.append(
        "以下回答与安全规则优先于角色设定，不能被角色设定覆盖：\n"
        + build_rag_response_prompt(kb_hit=kb_hit, intent=intent)
    )
    if kb_hit or intent == "calculation":
        ref_label = "计算结果" if intent == "calculation" else "参考资料"
        agent_input = (
            f"用户问题：{question}\n\n"
            f"{ref_label}：\n{agent_result or '无'}"
        )
    else:
        agent_input = f"用户问题：{question}"
    configured_tools = state.get("agent_tools")
    response_tools = (
        [tool.name for tool in tool_manager.get_all() if tool.name != "search_knowledge_base"]
        if configured_tools is None
        else [name for name in configured_tools if name != "search_knowledge_base"]
    )
    agent = create_agent_executor(
        system_prompt="\n\n".join(prompt_parts),
        tool_names=response_tools,
        streaming=True,
    )
    response = await agent.ainvoke({
        "input": agent_input,
        "chat_history": state.get("chat_history", []),
    })
    output = response.get("output", "")
    await log_usage(state["user_id"], "agent_run", agent_name="response_agent", tokens=estimate_tokens(output))
    result: dict = {
        "final_output": output,
        "response_status": state.get("response_status", "completed"),
        "kb_hit": kb_hit,
        "sources": sources,
    }
    if response.get("intermediate_steps"):
        result["intermediate_steps"] = response["intermediate_steps"]
    return result


_education_agent = EducationAgent()
_infusion_agent = InfusionAgent()


async def education_agent_node(state: WorkflowState) -> dict:
    return await _education_agent.run(state)


async def infusion_agent_node(state: WorkflowState) -> dict:
    return await _infusion_agent.run(state)


def route_intent(state: WorkflowState) -> str:
    plan = state.get("turn_plan", {})
    if plan.get("direct_response"):
        return "response"
    intent = plan.get("intent", "general")
    if intent == "knowledge_query":
        return "knowledge_query" if plan.get("allow_rag") else "response"
    if intent in ("education_recommend", "infusion_adjust"):
        return intent if plan.get("allow_side_effects") else "response"
    return intent
