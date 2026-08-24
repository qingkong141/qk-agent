from typing import Any

from fastapi import APIRouter

from app.dependencies import CurrentUser, DbSession
from app.services.conversation_access import conversation_external_user_id
from app.services.runtime_state import clear_user_context, get_user_context, set_user_context

router = APIRouter(prefix="/context", tags=["context"])


@router.post("/{workspace}")
async def set_context(workspace: str, context: dict[str, Any], user: CurrentUser, db: DbSession):
    await set_user_context(db, user["id"], conversation_external_user_id(user), workspace, context)
    return {"ok": True, "workspace": workspace}


@router.get("/{workspace}")
async def get_context(workspace: str, user: CurrentUser, db: DbSession):
    return await get_user_context(db, user["id"], conversation_external_user_id(user), workspace)


@router.delete("/{workspace}")
async def clear_context(workspace: str, user: CurrentUser, db: DbSession):
    await clear_user_context(db, user["id"], conversation_external_user_id(user), workspace)
    return {"ok": True}
