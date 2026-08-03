from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DbSession
from app.models.conversation import Conversation, Message
from app.models.usage_log import UsageLog
from app.schemas.monitor import MetricsResponse, ToolUsageItem

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(db: DbSession, user: CurrentUser):
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    total_conversations = await db.scalar(select(func.count()).select_from(Conversation)) or 0
    total_messages = await db.scalar(select(func.count()).select_from(Message)) or 0

    tool_calls_today = await db.scalar(
        select(func.count()).select_from(UsageLog).where(
            UsageLog.event_type == "tool_call",
            UsageLog.created_at >= today_start,
        )
    ) or 0

    tokens_used_today = await db.scalar(
        select(func.coalesce(func.sum(UsageLog.tokens), 0)).where(
            UsageLog.created_at >= today_start,
        )
    ) or 0

    tool_rows = await db.execute(
        select(UsageLog.tool_name, func.count())
        .where(UsageLog.event_type == "tool_call", UsageLog.created_at >= today_start)
        .group_by(UsageLog.tool_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    tool_usage = [
        ToolUsageItem(tool=row[0] or "unknown", count=row[1])
        for row in tool_rows.all()
    ]

    return MetricsResponse(
        total_conversations=total_conversations,
        total_messages=total_messages,
        tool_calls_today=tool_calls_today,
        tokens_used_today=tokens_used_today,
        tool_usage=tool_usage,
    )
