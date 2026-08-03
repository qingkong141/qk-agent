from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _ensure_sqlite_dir() -> None:
    """SQLite 不会自动创建父目录，启动前确保目录存在"""
    url = make_url(settings.DATABASE_URL)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        db_path = Path(url.database)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir()
engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_schema_patches() -> None:
    """兼容 create_all 建库但未跑 Alembic 的环境"""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    from sqlalchemy import text

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(messages)"))
        columns = {row[1] for row in result.fetchall()}
        if "sources" not in columns:
            await conn.execute(text("ALTER TABLE messages ADD COLUMN sources JSON"))
