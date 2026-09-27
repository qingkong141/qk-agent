"""Isolated cross-table SQL, subprocess bounds, persistence and ownership tests."""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api import datasets, modeling, offline
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.dataset import DatasetFolder, Dataset, DatasetFile
from app.models.data_model import ThemeDomain, DataModel
from app.models.offline_query import OfflineQuery


def migration_check():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        ThemeDomain.__table__.create(connection)
        spec = importlib.util.spec_from_file_location("migration012", Path(__file__).resolve().parents[1]/"alembic/versions/012_offline_queries.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert "offline_queries" in inspect(connection).get_table_names()
        assert len(inspect(connection).get_foreign_keys("offline_queries")) == 1
        module.downgrade()
        assert "offline_queries" not in inspect(connection).get_table_names()
    engine.dispose()


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    @event.listens_for(engine.sync_engine, "connect")
    def enable_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    async with engine.begin() as connection:
        for cls in (DatasetFolder, Dataset, DatasetFile, ThemeDomain, DataModel, OfflineQuery):
            await connection.run_sync(cls.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {"id":"owner", "auth_type":"jwt", "external_user_id":""}
    async def db_override():
        async with sessions() as session:
            yield session
    async def user_override():
        return principal
    app = FastAPI()
    for module in (datasets, modeling, offline):
        app.include_router(module.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    with tempfile.TemporaryDirectory() as directory:
        datasets.settings.DATASET_DIR = directory
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def call(method, path, status=200, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert response.status_code == status, (method,path,response.status_code,response.text)
                return response.json()
            catalog = "/studio/modeling"
            root = "/studio/offline"
            domain = await call("POST", catalog+"/domains",201,json={"name":"设备分析"})
            other_domain = await call("POST", catalog+"/domains",201,json={"name":"其他主题"})
            folder = await call("POST","/datasets/folders",201,json={"name":"private"})
            dataset = await call("POST","/datasets",201,json={"name":"records","folder_id":folder["id"]})
            async def model(name, rows):
                file = await call("POST",f"/datasets/{dataset['id']}/files",201,data={"modality":"structured"},files={"file":(name+".json",json.dumps(rows).encode())})
                fields = modeling.infer(rows)
                return await call("POST",catalog+"/models",201,json={"name":name,"domain_id":domain["id"],"table_name":name,"layer":"DWD","fields":fields,"source_file_id":file["id"]})
            readings = await model("readings",[{"device_id":f"D{n%3}","battery":n,"time":"2026-09-27T09:00:00","extra":{"ok":True}} for n in range(120)])
            devices = await model("devices",[{"device_id":"D0","ward":"A"},{"device_id":"D1","ward":"B"}])
            payload = {"domain_id":domain["id"],"model_ids":[readings["id"],devices["id"]],"row_limit":1000,"sql":"SELECT d.ward, COUNT(*) AS n, SUM(r.battery) AS total FROM readings r LEFT JOIN devices d ON r.device_id=d.device_id GROUP BY d.ward ORDER BY d.ward"}
            result = await call("POST",root+"/run",json=payload)
            assert result["rows"] == [[None,40,2420],["A",40,2340],["B",40,2380]],result
            assert result["sources"][0]["row_count"] == 120 and not result["truncated"]
            async def query(sql, status=200, **changes):
                return await call("POST",root+"/run",status,json={**payload,"sql":sql,**changes})
            result = await query("SELECT r.device_id,d.device_id FROM readings r INNER JOIN devices d ON r.device_id=d.device_id WHERE r.battery<2 ORDER BY r.battery")
            assert result["columns"] == ["device_id","device_id"] and result["rows"] == [["D0","D0"],["D1","D1"]]
            result = await query("WITH latest AS (SELECT * FROM readings WHERE battery >= 100) SELECT COUNT(*) FROM latest")
            assert result["rows"] == [[20]]
            assert (await query("SELECT battery FROM readings WHERE battery=0"))["rows"] == [[0]]
            assert (await query("SELECT battery FROM readings WHERE battery<0"))["columns"] == ["battery"]
            result = await query("SELECT battery FROM readings ORDER BY battery",row_limit=2)
            assert result["rows"] == [[0],[1]] and result["truncated"]
            result = await query("SELECT 9007199254740993 AS code")
            assert result["rows"] == [["9007199254740993"]] and result["large_integer_columns"] == [0]
            assert (await query("SELECT json_extract(extra,'$.ok') FROM readings LIMIT 1"))["rows"] == [[1]]
            assert (await query("SELECT '" + "设" * 7000 + "'"))["rows"] == [["设" * 7000]]
            for sql in ["DELETE FROM readings", "DROP TABLE readings", "CREATE TABLE stolen(x)", "ATTACH DATABASE ':memory:' AS other", "PRAGMA database_list", "SELECT * FROM sqlite_master", "SELECT load_extension('x')", "SELECT randomblob(999999999)", "SELECT * FROM pragma_table_info('readings')", "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT SUM(x) FROM c", "SELECT 1; SELECT 2", "SELECT * FROM missing", "SELECT bad FROM readings", "SELECT 1e999"]:
                await query(sql,400)
            await query("SELECT COUNT(*) FROM readings a, readings b, readings c, readings d",408)
            assert (await query("SELECT COUNT(*) FROM readings"))["rows"] == [[120]]
            await query("SELECT * FROM devices",400,model_ids=[readings["id"]])
            await query("SELECT 1",404,model_ids=["missing"])
            await query("SELECT 1",400,domain_id=other_domain["id"])
            schema = await call("POST",catalog+"/models",201,json={"name":"unbound","domain_id":domain["id"],"table_name":"unbound","layer":"DIM","fields":readings["fields"]})
            await query("SELECT 1",400,model_ids=[schema["id"]])
            saved = await call("POST",root+"/queries",201,json={**payload,"name":"按科室汇总"})
            restored = await call("GET",root+"/queries/"+saved["id"])
            assert restored["sql"] == payload["sql"] and restored["model_ids"] == payload["model_ids"]
            await call("POST",root+"/queries",409,json={**payload,"name":"按科室汇总"})
            await call("POST",root+"/queries",400,json={**payload,"sql":"DELETE FROM readings","name":"invalid"})
            edited = await call("PUT",root+"/queries/"+saved["id"],json={**payload,"name":"按科室汇总更新","expected_revision":1})
            assert edited["revision"] == 2
            await call("PUT",root+"/queries/"+saved["id"],409,json={**payload,"name":"stale","expected_revision":1})
            principal["id"] = "other"
            assert await call("GET",root+"/queries") == []
            await call("GET",root+"/queries/"+saved["id"],404)
            await query(payload["sql"],404)
            principal.update(id="owner",auth_type="api_key",external_user_id="person")
            await query(payload["sql"],404)
            principal["external_user_id"]=""
            await call("GET",root+"/queries",400)
            principal["auth_type"]="jwt"
            for item in (readings,devices,schema):
                await call("DELETE",catalog+"/models/"+item["id"])
            await call("DELETE",catalog+"/domains/"+domain["id"],409)
            await call("DELETE",root+"/queries/"+saved["id"])
            await call("DELETE",catalog+"/domains/"+domain["id"])
    await engine.dispose()
    print("PASS: real joins/CTE/aggregates over full input, null/zero/duplicate columns, result limits, read-only restrictions, compute timeout, saved queries, revisions, ownership and migration")


if __name__ == "__main__":
    migration_check()
    asyncio.run(main())
