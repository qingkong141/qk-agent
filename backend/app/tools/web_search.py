from langchain_core.tools import tool


@tool
def web_search(query: str) -> str:
    """搜索互联网获取最新信息（可选工具，需配置搜索 API）。"""
    return f"外部搜索功能尚未配置。查询: {query}"
