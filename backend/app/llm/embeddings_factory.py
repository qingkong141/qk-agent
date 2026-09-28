from langchain_core.embeddings import Embeddings

from app.config import settings


def _resolve_provider() -> str:
    if settings.EMBEDDING_PROVIDER:
        return settings.EMBEDDING_PROVIDER
    if settings.LLM_PROVIDER == "ollama":
        return "ollama"
    if settings.OPENAI_API_KEY:
        return "openai"
    if settings.DASHSCOPE_API_KEY:
        return "dashscope"
    raise ValueError(
        "未配置 Embedding 服务。请设置 EMBEDDING_PROVIDER=ollama|openai|dashscope，"
        "或配置 LLM_PROVIDER=ollama / OPENAI_API_KEY / DASHSCOPE_API_KEY"
    )


def create_embeddings() -> Embeddings:
    provider = _resolve_provider()

    if provider == "disabled":
        raise ValueError("未启用文档向量检索。请配置可用的 Embedding 服务后再使用此功能。")

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(
            model=settings.EMBEDDING_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
        )

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        if not settings.OPENAI_API_KEY:
            raise ValueError("EMBEDDING_PROVIDER=openai 但未配置 OPENAI_API_KEY")
        return OpenAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE or None,
        )

    if provider == "dashscope":
        from langchain_community.embeddings import DashScopeEmbeddings
        if not settings.DASHSCOPE_API_KEY:
            raise ValueError("EMBEDDING_PROVIDER=dashscope 但未配置 DASHSCOPE_API_KEY")
        model = settings.EMBEDDING_MODEL
        if model.startswith("text-embedding-3"):
            model = "text-embedding-v2"
        return DashScopeEmbeddings(
            model=model,
            dashscope_api_key=settings.DASHSCOPE_API_KEY,
        )

    raise ValueError(f"不支持的 EMBEDDING_PROVIDER: {provider}")
