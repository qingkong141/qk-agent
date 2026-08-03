from typing import Any

from fastapi import APIRouter

from app.api.chat import _context_store
from app.dependencies import CurrentUser

router = APIRouter(prefix="/context", tags=["context"])


@router.post("/{workspace}")
async def set_context(workspace: str, context: dict[str, Any], user: CurrentUser):
    _context_store[workspace] = context
    return {"ok": True, "workspace": workspace}


@router.get("/{workspace}")
async def get_context(workspace: str, user: CurrentUser):
    return _context_store.get(workspace, {})


@router.delete("/{workspace}")
async def clear_context(workspace: str, user: CurrentUser):
    _context_store.pop(workspace, None)
    return {"ok": True}
