import re
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from loguru import logger

_EMAIL_RE = re.compile(r"[\w.-]+@[\w.-]+\.\w+")
_PHONE_RE = re.compile(r"1[3-9]\d{9}")


def redact_pii(text: str, max_len: int = 2000) -> str:
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    return text[:max_len]


class LLMLoggingCallback(AsyncCallbackHandler):
    """记录 LLM 调用，日志中脱敏 PII"""

    async def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs):
        model = serialized.get("kwargs", {}).get("model", serialized.get("name", "unknown"))
        preview = redact_pii(" | ".join(prompts))
        logger.bind(llm=True).info(f"LLM start model={model} prompts={preview}")

    async def on_llm_end(self, response, **kwargs):
        generations = getattr(response, "generations", None) or []
        texts = []
        for gen_list in generations:
            for gen in gen_list:
                texts.append(getattr(gen, "text", str(gen)))
        output = redact_pii(" ".join(texts))
        logger.bind(llm=True).info(f"LLM end output={output}")

    async def on_llm_error(self, error: BaseException, **kwargs):
        logger.bind(llm=True).error(f"LLM error: {error}")


def get_llm_callbacks(extra: list | None = None) -> list:
    callbacks = [LLMLoggingCallback()]
    if extra:
        callbacks.extend(extra)
    return callbacks
