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
        mcp_columns = {row[1] for row in (await conn.execute(text("PRAGMA table_info(studio_mcp_services)"))).fetchall()}
        if mcp_columns and 'query_name' not in mcp_columns:
            await conn.execute(text("ALTER TABLE studio_mcp_services ADD COLUMN query_name VARCHAR(100) DEFAULT 'key' NOT NULL"))
        if mcp_columns and 'stdio_profile' not in mcp_columns:
            await conn.execute(text("ALTER TABLE studio_mcp_services ADD COLUMN stdio_profile VARCHAR(100) DEFAULT '' NOT NULL"))
        conversation_result = await conn.execute(text("PRAGMA table_info(conversations)"))
        conversation_columns = {row[1] for row in conversation_result.fetchall()}
        if "external_user_id" not in conversation_columns:
            await conn.execute(text(
                "ALTER TABLE conversations ADD COLUMN external_user_id VARCHAR(255) DEFAULT '' NOT NULL"
            ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_conversations_external_user_id "
            "ON conversations (external_user_id)"
        ))

        result = await conn.execute(text("PRAGMA table_info(messages)"))
        columns = {row[1] for row in result.fetchall()}
        if "sources" not in columns:
            await conn.execute(text("ALTER TABLE messages ADD COLUMN sources JSON"))
        if "status" not in columns:
            await conn.execute(text("ALTER TABLE messages ADD COLUMN status VARCHAR(20) DEFAULT 'completed' NOT NULL"))
        if "message_metadata" not in columns:
            await conn.execute(text("ALTER TABLE messages ADD COLUMN message_metadata JSON"))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id VARCHAR(36) PRIMARY KEY,
                conversation_id VARCHAR(36) NOT NULL,
                user_message_id VARCHAR(36),
                assistant_message_id VARCHAR(36),
                status VARCHAR(20) DEFAULT 'running' NOT NULL,
                run_metadata JSON,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations (id),
                FOREIGN KEY(user_message_id) REFERENCES messages (id),
                FOREIGN KEY(assistant_message_id) REFERENCES messages (id)
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_runs_conversation_id ON agent_runs (conversation_id)"
        ))

        clinical_result = await conn.execute(text("PRAGMA table_info(clinical_decisions)"))
        clinical_columns = {row[1] for row in clinical_result.fetchall()}
        if "owner_id" not in clinical_columns:
            await conn.execute(text(
                "ALTER TABLE clinical_decisions ADD COLUMN owner_id VARCHAR(36) DEFAULT '' NOT NULL"
            ))
        if "external_user_id" not in clinical_columns:
            await conn.execute(text(
                "ALTER TABLE clinical_decisions ADD COLUMN external_user_id VARCHAR(255) DEFAULT '' NOT NULL"
            ))
        if "idempotency_key" not in clinical_columns:
            await conn.execute(text(
                "ALTER TABLE clinical_decisions ADD COLUMN idempotency_key VARCHAR(128) DEFAULT '' NOT NULL"
            ))
            await conn.execute(text(
                "UPDATE clinical_decisions SET idempotency_key = id WHERE idempotency_key = ''"
            ))
        if "version" not in clinical_columns:
            await conn.execute(text(
                "ALTER TABLE clinical_decisions ADD COLUMN version INTEGER DEFAULT 0 NOT NULL"
            ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_clinical_decision_idempotency "
            "ON clinical_decisions (owner_id, external_user_id, idempotency_key)"
        ))
