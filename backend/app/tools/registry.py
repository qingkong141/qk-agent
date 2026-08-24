import asyncio
import time

from langchain_core.tools import StructuredTool
from loguru import logger

from app.core.context import get_request_context
from app.services.usage_tracker import estimate_tokens, log_usage


TOOL_POLICIES = {
    "confirm_education_push": "approval_required",
}


class ToolManager:
    """工具管理器：注册、查询、获取 LangChain Tool 列表"""

    _instance = None
    _tools: dict[str, StructuredTool] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, t: StructuredTool):
        original_func = t.coroutine or t.func
        tool_name = t.name

        async def wrapped(*args, **kwargs):
            start = time.time()
            try:
                policy = TOOL_POLICIES.get(tool_name, "allowed")
                if policy == "approval_required":
                    from app.db.session import async_session
                    from app.services.runtime_state import consume_tool_approval, request_tool_approval

                    ctx = get_request_context()
                    arguments = dict(kwargs)
                    if args:
                        arguments["_args"] = list(args)
                    required = ("user_id", "conversation_id")
                    if any(not ctx.get(field) for field in required):
                        return "该工具需要在已认证的会话中执行。"
                    async with async_session() as db:
                        approval_state = "denied"
                        if ctx.get("approval_id"):
                            approval_state = await consume_tool_approval(
                                db, approval_id=ctx["approval_id"], owner_id=ctx["user_id"],
                                external_user_id=ctx.get("external_user_id", ""),
                                conversation_id=ctx["conversation_id"], tool_name=tool_name,
                                arguments=arguments,
                            )
                        if approval_state == "already_consumed":
                            return "该审批对应的操作已经执行，不会重复执行。"
                        if approval_state != "execute":
                            approval = await request_tool_approval(
                                db, owner_id=ctx["user_id"],
                                external_user_id=ctx.get("external_user_id", ""),
                                conversation_id=ctx["conversation_id"], run_id=ctx.get("run_id"),
                                tool_name=tool_name, arguments=arguments,
                            )
                            return f"该操作需要明确审批，approval_id: {approval.id}"
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
