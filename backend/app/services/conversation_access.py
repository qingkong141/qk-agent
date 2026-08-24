from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


def conversation_external_user_id(principal: dict) -> str:
    external_user_id = principal.get("external_user_id", "").strip()
    if principal.get("auth_type") == "api_key" and not external_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API Key调用会话接口时必须提供 X-End-User-ID",
        )
    return external_user_id


async def get_or_create_owned_conversation(
    db: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    external_user_id: str = "",
    workspace: str = "default",
) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is not None:
        if conversation.user_id != user_id or conversation.external_user_id != external_user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        return conversation

    conversation = Conversation(
        id=conversation_id,
        user_id=user_id,
        external_user_id=external_user_id,
        title="新对话",
        workspace=workspace,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation
