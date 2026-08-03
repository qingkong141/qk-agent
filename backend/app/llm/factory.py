from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings


def create_chat_model(model_name: str | None = None, *, streaming: bool = False) -> BaseChatModel:
    """根据环境变量创建 LangChain ChatModel 实例"""
    provider = settings.LLM_PROVIDER
    model = model_name or settings.LLM_MODEL

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=settings.TEMPERATURE,
            max_tokens=settings.MAX_TOKENS,
            api_key=settings.ANTHROPIC_API_KEY or None,
            streaming=streaming,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        kwargs: dict = {
            "model": model,
            "temperature": settings.TEMPERATURE,
            "max_tokens": settings.MAX_TOKENS,
            "api_key": settings.OPENAI_API_KEY or None,
            "streaming": streaming,
        }
        if settings.OPENAI_API_BASE:
            kwargs["base_url"] = settings.OPENAI_API_BASE
        return ChatOpenAI(**kwargs)
    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model,
            temperature=settings.TEMPERATURE,
            base_url=settings.OLLAMA_BASE_URL,
            num_predict=settings.MAX_TOKENS,
        )
    elif provider == "dashscope":
        from langchain_community.chat_models.tongyi import ChatTongyi
        return ChatTongyi(
            model=model,
            temperature=settings.TEMPERATURE,
            max_tokens=settings.MAX_TOKENS,
            dashscope_api_key=settings.DASHSCOPE_API_KEY or None,
            streaming=streaming,
        )
    else:
        raise ValueError(f"不支持的 LLM Provider: {provider}")
