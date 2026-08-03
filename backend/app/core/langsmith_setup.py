import os

from loguru import logger

from app.config import settings


def setup_langsmith() -> None:
    if not settings.LANGCHAIN_TRACING_V2:
        return
    if not settings.LANGCHAIN_API_KEY:
        logger.warning("LANGCHAIN_TRACING_V2=true 但未配置 LANGCHAIN_API_KEY，跳过 LangSmith")
        return

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
    logger.info(f"LangSmith tracing enabled, project={settings.LANGCHAIN_PROJECT}")
