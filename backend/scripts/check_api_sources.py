"""Check how MessageResponse serializes sources from DB."""
import asyncio
import json

from sqlalchemy import select

from app.db.session import async_session
from app.models.conversation import Message
from app.schemas.agent import MessageResponse


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(Message)
            .where(Message.role == "assistant")
            .order_by(Message.created_at.desc())
            .limit(3)
        )
        for msg in result.scalars().all():
            resp = MessageResponse.model_validate(msg)
            print("---")
            print("id:", msg.id)
            print("sources type:", type(msg.sources), msg.sources is not None)
            print("json:", json.dumps(resp.model_dump(), ensure_ascii=False)[:500])


if __name__ == "__main__":
    asyncio.run(main())
