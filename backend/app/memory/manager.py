from langchain_core.messages import BaseMessage
from sqlalchemy import delete

from app.db.session import async_session
from app.memory.history import load_chat_messages, save_chat_messages
from app.models.conversation import Message


class MemoryManager:
    """对话记忆管理器 — 基于 messages 表持久化"""

    async def get_messages(self, conversation_id: str, limit: int = 40) -> list[BaseMessage]:
        return await load_chat_messages(conversation_id, limit=limit)

    async def append(
        self,
        conversation_id: str,
        human_content: str,
        ai_content: str,
        *,
        sources: list[dict] | None = None,
    ) -> None:
        await save_chat_messages(
            conversation_id,
            human_content,
            ai_content,
            sources=sources,
        )

    async def clear(self, conversation_id: str) -> None:
        async with async_session() as db:
            await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
            await db.commit()


memory_manager = MemoryManager()
