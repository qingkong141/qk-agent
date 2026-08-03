import uuid

from langchain_core.tools import tool
from sqlalchemy import select

from app.core.context import get_request_context
from app.db.session import async_session
from app.models.long_term_memory import LongTermMemory


@tool
async def save_memory(key: str, value: str) -> str:
    """保存长期记忆，供后续跨对话召回。key 为记忆标识，value 为要记住的内容。"""
    ctx = get_request_context()
    user_id = ctx.get("user_id")
    if not user_id:
        return "无法保存记忆：未识别用户身份"

    workspace = ctx.get("workspace", "default")
    async with async_session() as db:
        result = await db.execute(
            select(LongTermMemory).where(
                LongTermMemory.user_id == user_id,
                LongTermMemory.workspace == workspace,
                LongTermMemory.key == key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(LongTermMemory(
                id=str(uuid.uuid4()),
                user_id=user_id,
                workspace=workspace,
                key=key,
                value=value,
            ))
        await db.commit()
    return f"已保存记忆 [{key}]"


@tool
async def recall_memory(key: str) -> str:
    """召回之前保存的长期记忆。"""
    ctx = get_request_context()
    user_id = ctx.get("user_id")
    if not user_id:
        return "无法召回记忆：未识别用户身份"

    workspace = ctx.get("workspace", "default")
    async with async_session() as db:
        result = await db.execute(
            select(LongTermMemory).where(
                LongTermMemory.user_id == user_id,
                LongTermMemory.workspace == workspace,
                LongTermMemory.key == key,
            )
        )
        memory = result.scalar_one_or_none()
    if not memory:
        return f"未找到记忆 [{key}]"
    return memory.value


save_memory_tool = save_memory
recall_memory_tool = recall_memory
