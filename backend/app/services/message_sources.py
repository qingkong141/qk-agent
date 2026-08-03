from app.rag.pipeline import is_kb_miss, parse_knowledge_sources


def _tool_name(step_tool) -> str:
    if hasattr(step_tool, "tool"):
        return str(step_tool.tool)
    return str(step_tool)


def extract_sources_from_steps(intermediate_steps: list | None) -> list[dict]:
    """从 Agent / 工作流 intermediate_steps 中提取知识库引用"""
    for step in intermediate_steps or []:
        if not isinstance(step, (list, tuple)) or len(step) < 2:
            continue
        if _tool_name(step[0]) != "search_knowledge_base":
            continue
        text = str(step[1])
        if is_kb_miss(text):
            continue
        sources = parse_knowledge_sources(text)
        if sources:
            return sources
    return []
