import uuid

from app.db.session import async_session
from app.models.usage_log import UsageLog


async def log_usage(
    user_id: str,
    event_type: str,
    *,
    agent_name: str | None = None,
    tool_name: str | None = None,
    tokens: int = 0,
) -> None:
    async with async_session() as db:
        db.add(UsageLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            event_type=event_type,
            agent_name=agent_name,
            tool_name=tool_name,
            tokens=tokens,
        ))
        await db.commit()


def estimate_tokens(text: str) -> int:
    return max(len(text) // 4, 1)
