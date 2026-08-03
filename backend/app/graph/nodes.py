from langchain_core.messages import HumanMessage, SystemMessage

from app.core.agent_factory import create_agent_executor
from app.core.prompts import build_rag_response_prompt, extract_user_input
from app.graph.agents.education import EducationAgent
from app.graph.agents.infusion import InfusionAgent
from app.graph.state import WorkflowState
from app.llm.factory import create_chat_model
from app.rag.pipeline import is_kb_miss, parse_knowledge_sources, rag_pipeline
from app.services.usage_tracker import estimate_tokens, log_usage


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
    """LLM 语义分析意图，一次调用覆盖所有场景"""
    llm = create_chat_model()
    prompt = f"""分析用户意图，只回复以下词之一（不要其他文字）：

knowledge_query      - 需要查知识库或文档（询问医学知识、规范、概念解释等）
calculation          - 需要数学计算（加减乘除、数值运算）
education_recommend  - 需要推荐或推送宣教内容给患者
infusion_adjust      - 需要调整输液滴速（改变、增加、减少泵速）
general              - 一般对话、闲聊、或以上都不匹配

注意区分：
- "宣教是什么意思" → knowledge_query（问概念，不是要推送）
- "给患者推荐宣教" → education_recommend（要推送）
- "3床滴速多少" → general（查看状态，不是调整）
- "把3床滴速调到40" → infusion_adjust（要调整）

用户输入：{state['user_input']}"""
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = response.content if isinstance(response.content, str) else str(response.content)
    intent = content.strip().lower()
    # 规范化：从 LLM 返回中提取有效 intent
    for valid in ("knowledge_query", "calculation", "education_recommend", "infusion_adjust", "general"):
        if valid in intent:
            await log_usage(state["user_id"], "agent_run", agent_name="supervisor", tokens=estimate_tokens(content))
            return {"intent": valid}
    # 兜底
    await log_usage(state["user_id"], "agent_run", agent_name="supervisor", tokens=estimate_tokens(content))
    return {"intent": "general"}


async def kb_agent_node(state: WorkflowState) -> dict:
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
    intermediate_steps: list = []

    if intent == "general" and not agent_result:
        query = extract_user_input(state["user_input"])
        agent_result, kb_hit, _ = rag_pipeline.resolve_search(query)
        intermediate_steps = [(_ToolStep("search_knowledge_base"), agent_result)]

    sources = state.get("sources") or []
    if intent == "general" and kb_hit and agent_result and not is_kb_miss(agent_result):
        sources = parse_knowledge_sources(agent_result)

    if intent != "calculation":
        kb_hit = kb_hit and not is_kb_miss(agent_result)

    llm = create_chat_model(streaming=True)
    system = SystemMessage(content=build_rag_response_prompt(kb_hit=kb_hit, intent=intent))
    question = extract_user_input(state["user_input"])
    if kb_hit or intent == "calculation":
        ref_label = "计算结果" if intent == "calculation" else "参考资料"
        human = HumanMessage(content=(
            f"用户问题：{question}\n\n"
            f"{ref_label}：\n{agent_result or '无'}"
        ))
    else:
        human = HumanMessage(content=f"用户问题：{question}")
    response = await llm.ainvoke([system, human])
    output = response.content if isinstance(response.content, str) else str(response.content)
    await log_usage(state["user_id"], "agent_run", agent_name="response_agent", tokens=estimate_tokens(output))
    result: dict = {"final_output": output, "kb_hit": kb_hit, "sources": sources}
    if intermediate_steps:
        result["intermediate_steps"] = intermediate_steps
    return result


_education_agent = EducationAgent()
_infusion_agent = InfusionAgent()


async def education_agent_node(state: WorkflowState) -> dict:
    return await _education_agent.run(state)


async def infusion_agent_node(state: WorkflowState) -> dict:
    return await _infusion_agent.run(state)


def route_intent(state: WorkflowState) -> str:
    return state.get("intent", "general")
