import pytest_asyncio

from app.db.session import ensure_schema_patches, init_db


@pytest_asyncio.fixture(scope="session", autouse=True)
async def initialize_test_database():
    await init_db()
    await ensure_schema_patches()
