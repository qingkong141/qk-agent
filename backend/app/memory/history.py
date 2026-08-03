import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select

from app.db.session import async_session
from app.models.conversation import Conversation, Message
from app.services.conversation_title import derive_conversation_title, is_default_conversation_title


def _to_langchain(msg: Message) -> BaseMessage:
    if msg.role in ("user", "human"):
        return HumanMessage(content=msg.content)
    return AIMessage(content=msg.content)


async def load_chat_messages(conversation_id: str, limit: int = 40) -> list[BaseMessage]:
    async with async_session() as db:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        rows = result.scalars().all()
        return [_to_langchain(m) for m in rows[-limit:]]


async def save_chat_messages(
    conversation_id: str,
    human_content: str,
    ai_content: str,
    *,
    sources: list[dict] | None = None,
) -> None:
    async with async_session() as db:
        conv = await db.get(Conversation, conversation_id)
        if not conv:
            return

        if is_default_conversation_title(conv.title) and human_content.strip():
            conv.title = derive_conversation_title(human_content)
        conv.updated_at = datetime.now(timezone.utc)

        db.add(Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="user",
            content=human_content,
        ))
        db.add(Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content=ai_content,
            sources=sources or None,
        ))
        await db.commit()
