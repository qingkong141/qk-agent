import asyncio
import time

from langchain_core.tools import StructuredTool
from loguru import logger

from app.core.context import get_request_context
from app.services.usage_tracker import estimate_tokens, log_usage


class ToolManager:
    """工具管理器：注册、查询、获取 LangChain Tool 列表"""

    _instance = None
    _tools: dict[str, StructuredTool] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, t: StructuredTool):
        original_func = t.func
        tool_name = t.name

        async def wrapped(*args, **kwargs):
            start = time.time()
            try:
                if asyncio.iscoroutinefunction(original_func):
                    result = await original_func(*args, **kwargs)
                else:
                    result = original_func(*args, **kwargs)
                logger.info(f"Tool [{tool_name}] completed in {time.time() - start:.2f}s")

                ctx = get_request_context()
                user_id = ctx.get("user_id")
                if user_id:
                    await log_usage(
                        user_id,
                        "tool_call",
                        tool_name=tool_name,
                        tokens=estimate_tokens(str(result)),
                    )
                return result
            except Exception as e:
                logger.error(f"Tool [{tool_name}] failed: {e}")
                return f"工具执行出错: {str(e)}"

        wrapped_tool = StructuredTool(
            name=t.name,
            description=t.description,
            func=wrapped,
            coroutine=wrapped,
            args_schema=t.args_schema,
        )
        self._tools[t.name] = wrapped_tool

    def get_all(self) -> list[StructuredTool]:
        return list(self._tools.values())

    def get(self, name: str) -> StructuredTool | None:
        return self._tools.get(name)


tool_manager = ToolManager()
