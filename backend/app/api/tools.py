from fastapi import APIRouter

from app.dependencies import CurrentUser
from app.tools.registry import tool_manager

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
async def list_tools(user: CurrentUser):
    tools = tool_manager.get_all()
    return [{"name": t.name, "description": t.description} for t in tools]
