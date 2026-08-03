import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from app.dependencies import CurrentUser, DbSession
from app.models.conversation import Conversation, Message
from app.schemas.agent import ConversationCreate, ConversationResponse, MessageResponse
from app.services.conversation_title import derive_conversation_title, is_default_conversation_title

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _get_user_conversation(conversation_id: str, user_id: str, db) -> Conversation | None:
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(db: DbSession, user: CurrentUser):
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == user["id"]).order_by(Conversation.updated_at.desc())
    )
    conversations = result.scalars().all()
    changed = False
    for conv in conversations:
        if not is_default_conversation_title(conv.title):
            continue
        msg_result = await db.execute(
            select(Message.content)
            .where(Message.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.created_at.asc())
            .limit(1)
        )
        first_message = msg_result.scalar_one_or_none()
        if first_message and first_message.strip():
            conv.title = derive_conversation_title(first_message)
            changed = True
    if changed:
        await db.commit()
    return conversations


@router.post("", response_model=ConversationResponse)
async def create_conversation(data: ConversationCreate, db: DbSession, user: CurrentUser):
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=user["id"],
        title=data.title or "新对话",
        workspace=data.workspace,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(conversation_id: str, db: DbSession, user: CurrentUser):
    conv = await _get_user_conversation(conversation_id, user["id"], db)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return result.scalars().all()


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, db: DbSession, user: CurrentUser):
    conv = await _get_user_conversation(conversation_id, user["id"], db)
    if not conv:
        return {"ok": True}
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await db.delete(conv)
    await db.commit()
    return {"ok": True}
