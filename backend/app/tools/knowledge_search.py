from langchain_core.tools import tool

from app.rag.pipeline import rag_pipeline


@tool
def search_knowledge_base(query: str, max_results: int = 5) -> str:
    """搜索内部知识库，获取参考资料。"""
    text, _, _ = rag_pipeline.resolve_search(query)
    return text


search_knowledge_base_tool = search_knowledge_base
