"""Isolated API lifecycle tests; persistent development data is not touched."""
import asyncio
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import data_services, studio
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {"id": "owner", "auth_type": "jwt", "external_user_id": ""}
    async def db_override():
        async with sessions() as db:
            yield db
    async def user_override():
        return principal
    app = FastAPI()
    app.include_router(data_services.router)
    app.include_router(studio.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    app.dependency_overrides[data_services.reader] = user_override
    async with sessions() as db:
        db.add_all([StudioArtifact(id="pipe", name="输液处理管道", kind="pipeline", config={}, revision=2, owner_id="owner", external_user_id=""),
                    StudioArtifact(id="other-pipe", name="其他账号", kind="pipeline", config={}, revision=1, owner_id="other", external_user_id="")])
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        base = "/studio/data-services"
        rows = [{"deviceId": "INF-001", "battery": 0, "status": False}, {"deviceId": "INF-002", "battery": None}, {"deviceId": "INF-003", "battery": 61, "extra": {"alarm": [1, 2]}}]
        body = {"name": "处理结果接口", "pipeline_id": "pipe", "pipeline_revision": 2, "rows": rows}
        for change, status in [({"pipeline_id": "other-pipe"}, 404), ({"pipeline_revision": 1}, 409), ({"name": " "}, 422), ({"rows": [{" ": 1}]}, 422), ({"rows": [{}] * 10001}, 422), ({"rows": [{str(i): i for i in range(201)}]}, 422), ({"rows": [{"data": "x" * (2 * 1024 * 1024)}]}, 422)]:
            response = await client.post(base, json={**body, **change})
            assert response.status_code == status, response.text
        response = await client.post(base, json=body)
        assert response.status_code == 201, response.text
        item = response.json(); path = f"{base}/{item['id']}"
        assert item["row_count"] == 3 and item["pipeline_revision"] == 2 and "rows" not in item
        assert item["fields"] == ["deviceId", "battery", "status", "extra"]
        assert len((await client.get(base)).json()) == 1
        assert all(entry["kind"] != "data_service" for entry in (await client.get('/studio/artifacts')).json())
        data = await client.get(path + "/data?page_size=2")
        assert data.json()["rows"] == rows[:2] and data.headers["cache-control"] == "no-store"
        assert (await client.get(path + "/data?page=2&page_size=2")).json()["rows"] == rows[2:]
        assert (await client.get(path + "/data?page=9")).json()["rows"] == []
        for query in ("page=0", "page_size=501", "page=1.2", "page_size=0"):
            assert (await client.get(path + "/data?" + query)).status_code == 422
        settings = {"name": item["name"], "description": "开放处理结果", "enabled": False, "expected_revision": 1}
        stopped = await client.put(path, json=settings)
        assert stopped.status_code == 200 and stopped.json()["revision"] == 2
        assert (await client.get(path + "/data")).status_code == 409
        assert (await client.put(path, json=settings)).status_code == 409
        assert (await client.put(path + "/result", json={**body, "expected_revision": 1})).status_code == 409
        updated = await client.put(path + "/result", json={**body, "rows": [{"battery": 88}], "expected_revision": 2})
        assert updated.status_code == 200 and updated.json()["revision"] == 3 and not updated.json()["enabled"]
        assert updated.json()["fields"] == ["battery"]
        assert (await client.put(path, json={**settings, "enabled": True, "expected_revision": 3})).status_code == 200
        assert (await client.get(path + "/data")).json()["rows"] == [{"battery": 88}]
        # Result is independent of later source edits and deletion.
        async with sessions() as db:
            pipe = await db.get(StudioArtifact, "pipe")
            pipe.revision = 3
            await db.commit()
        assert (await client.put(path + "/result", json={**body, "expected_revision": 4})).status_code == 409
        principal["id"] = "other"
        assert (await client.get(base)).json() == []
        for suffix in ("", "/data"):
            assert (await client.get(path + suffix)).status_code == 404
        assert (await client.put(path, json={**settings, "expected_revision": 4})).status_code == 404
        assert (await client.request("DELETE", path, json={"expected_revision": 4})).status_code == 404
        principal.update(id="owner", auth_type="api_key", external_user_id="")
        assert (await client.get(base)).status_code == 400
        principal["external_user_id"] = "user-b"
        assert (await client.get(path + "/data")).status_code == 404
        principal.update(auth_type="jwt", external_user_id="")
        assert (await client.request("DELETE", path, json={"expected_revision": 3})).status_code == 409
        assert (await client.request("DELETE", path, json={"expected_revision": 4})).status_code == 200
        assert (await client.get(path + "/data")).status_code == 404
        async with sessions() as db:
            assert await db.scalar(select(StudioArtifact).where(StudioArtifact.id == "pipe"))
        empty = (await client.post(base, json={**body, "pipeline_revision": 3, "rows": []})).json()
        assert (await client.get(empty["path"])).json()["total"] == 0
        assert empty["require_login"] is True
        access_path = f"{base}/{empty['id']}"
        # Use real read authentication: no headers must fail by default, including legacy records.
        del app.dependency_overrides[data_services.reader]
        assert (await client.get(empty["path"])).status_code == 401
        async with sessions() as db:
            legacy = await db.get(StudioArtifact, empty["id"])
            legacy.config = {key: value for key, value in legacy.config.items() if key != "require_login"}
            await db.commit()
        assert (await client.get(access_path)).json()["require_login"] is True
        assert (await client.get(empty["path"])).status_code == 401
        public_settings = {"name": empty["name"], "enabled": True, "require_login": False, "expected_revision": 1}
        assert (await client.put(access_path, json=public_settings)).status_code == 200
        assert (await client.get(empty["path"])).status_code == 200
        # Public mode ignores unrelated/expired caller credentials, but never opens management.
        assert (await client.get(empty["path"], headers={"X-Platform-Token": "expired"})).status_code == 200
        del app.dependency_overrides[get_current_user]
        assert (await client.get(base)).status_code == 401
        assert (await client.get(access_path)).status_code == 401
        assert (await client.put(access_path, json=public_settings)).status_code == 401
        assert (await client.request("DELETE", access_path, json={"expected_revision": 2})).status_code == 401
        app.dependency_overrides[get_current_user] = user_override
        replaced = await client.put(access_path + "/result", json={**body, "pipeline_revision": 3, "expected_revision": 2})
        assert replaced.json()["require_login"] is False
        assert (await client.get(empty["path"])).json()["rows"] == rows
        assert (await client.put(access_path, json={**public_settings, "enabled": False, "expected_revision": 3})).status_code == 200
        assert (await client.get(empty["path"])).status_code == 409
        assert (await client.put(access_path, json={**public_settings, "expected_revision": 4})).status_code == 200
        assert (await client.get(empty["path"])).status_code == 200
        assert (await client.put(access_path, json={**public_settings, "require_login": True, "expected_revision": 5})).status_code == 200
        assert (await client.get(empty["path"])).status_code == 401
    await engine.dispose()
    print("PASS: snapshots/pagination, limits, lifecycle/revisions, ownership, default/legacy authentication, opt-in anonymous data with protected management, mode preservation and revocation")


if __name__ == "__main__":
    asyncio.run(main())
