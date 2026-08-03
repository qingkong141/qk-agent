import sys

from loguru import logger

from app.config import settings


def setup_logging() -> None:
    logger.remove()
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, level="DEBUG" if settings.DEBUG else "INFO", format=log_format)
    logger.add(
        f"{settings.LOG_DIR}/app_{{time:YYYY-MM-DD}}.log",
        rotation="00:00",
        retention="30 days",
        level="INFO",
        format=log_format,
        encoding="utf-8",
    )
    logger.add(
        f"{settings.LOG_DIR}/llm_{{time:YYYY-MM-DD}}.log",
        rotation="00:00",
        retention="14 days",
        level="INFO",
        filter=lambda record: record["extra"].get("llm") is True,
        format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        encoding="utf-8",
    )
