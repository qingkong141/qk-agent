import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select

from app.db.session import async_session
from app.models.conversation import AgentRun, Conversation, Message
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
    status: str = "completed",
    run_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    async with async_session() as db:
        conv = await db.get(Conversation, conversation_id)
        if not conv:
            return

        if is_default_conversation_title(conv.title) and human_content.strip():
            conv.title = derive_conversation_title(human_content)
        conv.updated_at = datetime.now(timezone.utc)

        user_message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="user",
            content=human_content,
        )
        assistant_message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content=ai_content,
            status=status,
            sources=sources or None,
            message_metadata=metadata or None,
        )
        db.add(user_message)
        db.add(assistant_message)
        if run_id:
            db.add(AgentRun(
                id=run_id,
                conversation_id=conversation_id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                status=status,
                run_metadata=metadata or None,
                updated_at=datetime.now(timezone.utc),
            ))
        await db.commit()


async def get_last_interrupted_turn(conversation_id: str) -> dict | None:
    async with async_session() as db:
        result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        assistant = result.scalar_one_or_none()
        if not assistant or assistant.status != "interrupted":
            return None

        previous_result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role.in_(("user", "human")),
                Message.created_at <= assistant.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        user_message = previous_result.scalar_one_or_none()
        return {
            "assistant_message_id": assistant.id,
            "user_message": user_message.content if user_message else "",
            "partial_output": assistant.content,
            "metadata": assistant.message_metadata or {},
        }


async def get_last_needs_user_input_turn(conversation_id: str) -> dict | None:
    async with async_session() as db:
        result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        assistant = result.scalar_one_or_none()
        if not assistant or assistant.status != "needs_user_input":
            return None

        previous_result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role.in_(("user", "human")),
                Message.created_at <= assistant.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        user_message = previous_result.scalar_one_or_none()
        return {
            "assistant_message_id": assistant.id,
            "user_message": user_message.content if user_message else "",
            "assistant_prompt": assistant.content,
            "metadata": assistant.message_metadata or {},
        }
