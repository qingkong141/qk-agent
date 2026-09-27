"""Isolated model persistence, all-row validation, ownership and reference regressions."""
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
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import datasets, modeling
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.dataset import Dataset, DatasetFile, DatasetFolder
from app.models.data_model import DataModel, ThemeDomain


def check_validation_and_migration():
    fields = [modeling.ModelField(name="device", source="device", type="string", primary_key=True, nullable=False),
              modeling.ModelField(name="time", source="time", type="integer", primary_key=True, nullable=False)]
    result = modeling.evaluate([{"device":"A","time":1},{"device":"A","time":2},{"device":"A","time":1}], fields)
    assert result["invalid_rows"] == 1 and result["errors"][0]["row"] == 3
    result = modeling.evaluate([{"device":None,"time":1},{"device":"A","time":"1.5"}], fields)
    assert result["invalid_rows"] == 2
    assert modeling.infer([{"code":9007199254740993}])[0]["type"] == "string"
    schema = modeling.infer([{"\u8bbe\u5907":1,"field_1":2,"FIELD_1":3}])
    assert len({field["name"].lower() for field in schema}) == 3
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        for model in (DatasetFolder, Dataset, DatasetFile):
            model.__table__.create(connection)
        spec = importlib.util.spec_from_file_location("migration011", Path(__file__).resolve().parents[1] / "alembic/versions/011_data_models.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert {"data_models","theme_domains"}.issubset(inspect(connection).get_table_names())
        assert len(inspect(connection).get_foreign_keys("data_models")) == 2
        migration.downgrade()
        assert "data_models" not in inspect(connection).get_table_names()
    engine.dispose()


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    async with engine.begin() as connection:
        for model in (DatasetFolder, Dataset, DatasetFile, ThemeDomain, DataModel):
            await connection.run_sync(model.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {"id": "owner", "auth_type": "jwt", "external_user_id": ""}
    async def db_override():
        async with sessions() as session:
            yield session
    async def user_override():
        return principal
    app = FastAPI()
    app.include_router(datasets.router)
    app.include_router(modeling.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    with tempfile.TemporaryDirectory() as directory:
        datasets.settings.DATASET_DIR = directory
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def call(method, path, status=200, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert response.status_code == status, (method, path, response.status_code, response.text)
                return response.json()
            root = "/studio/modeling"
            domain = await call("POST", root + "/domains", 201, json={"name": "设备工况", "description": "业务主题"})
            await call("POST", root + "/domains", 409, json={"name": "设备工况"})
            folder = await call("POST", "/datasets/folders", 201, json={"name": "model source"})
            dataset = await call("POST", "/datasets", 201, json={"name": "records", "folder_id": folder["id"]})
            files_path = "/datasets/" + dataset["id"] + "/files"
            rows = [{"deviceId": f"INF-{n}", "battery": n, "time": "2026-09-27T09:00:00+08:00"} for n in range(61)]
            async def upload(name, value, modality="structured"):
                return await call("POST", files_path, 201, data={"modality": modality}, files={"file": (name, value)})
            source = await upload("readings.json", json.dumps(rows).encode())
            inferred = await call("GET", root + "/sources/" + source["id"])
            assert inferred["total"] == 61 and inferred["fields"][1]["type"] == "integer"
            fields = inferred["fields"]
            fields[0].update(primary_key=True, nullable=False, role="dimension")
            fields[1].update(type="number", role="measure")
            fields[2].update(type="datetime")
            fields.append({"name": "ward", "source": "", "type": "string", "nullable": True})
            payload = {"name": "设备采集", "domain_id": domain["id"], "table_name": "ods_device", "layer": "ODS", "fields": fields, "source_file_id": source["id"]}
            result = await call("POST", root + "/preview", json=payload)
            assert result["ok"] and result["total"] == 61 and len(result["rows"]) == 50
            assert result["rows"][0]["battery"] == 0 and result["rows"][0]["ward"] is None
            models = []
            for layer in ["ODS", "DWD", "DIM", "DWS", "ADS"]:
                model = await call("POST", root + "/models", 201, json={**payload, "name": layer, "layer": layer, "table_name": layer.lower() + "_device"})
                models.append(model)
                restored = await call("GET", root + "/models/" + model["id"])
                assert restored["fields"] == model["fields"] and restored["source_file_id"] == source["id"]
            assert len(await call("GET", root + "/models")) == 5
            await call("DELETE", root + "/domains/" + domain["id"], 409)
            await call("DELETE", files_path + "/" + source["id"], 409)
            await call("POST", root + "/models", 409, json=payload)
            edited = await call("PUT", root + "/models/" + models[0]["id"], json={**payload, "expected_revision": 1})
            assert edited["revision"] == 2
            await call("PUT", root + "/models/" + models[0]["id"], 409, json={**payload, "expected_revision": 1})
            await call("POST", root + "/models", 422, json={**payload, "fields": [fields[0], fields[0]]})
            await call("POST", root + "/models", 422, json={**payload, "fields": [{**fields[0], "nullable": True}]})
            await call("POST", root + "/models", 422, json={**payload, "table_name": "bad;drop table"})
            await call("POST", root + "/preview", 400, json={**payload, "fields": [{**fields[0], "source": "missing"}]})
            rows[-1]["battery"] = "not a number"
            rows[-2]["deviceId"] = rows[0]["deviceId"]
            bad = await upload("invalid.json", json.dumps(rows).encode())
            bad_payload = {**payload, "source_file_id": bad["id"], "table_name": "bad_data", "name": "bad data"}
            result = await call("POST", root + "/preview", json=bad_payload)
            assert not result["ok"] and result["invalid_rows"] == 2 and result["errors"][0]["row"] == 60
            await call("POST", root + "/models", 422, json=bad_payload)
            schema = await call("POST", root + "/models", 201, json={**payload, "name": "schema only", "table_name": "empty_schema", "source_file_id": None})
            result = await call("POST", root + "/preview", json={**payload, "source_file_id": None})
            assert not result["bound"]
            txt = await upload("notes.txt", b"text", "text")
            await call("GET", root + "/sources/" + txt["id"], 400)
            principal["id"] = "other"
            assert await call("GET", root + "/models") == []
            for path in ("/models/" + models[0]["id"], "/sources/" + source["id"]):
                await call("GET", root + path, 404)
            await call("POST", root + "/preview", 404, json=payload)
            await call("DELETE", root + "/domains/" + domain["id"], 404)
            principal.update(id="owner", auth_type="api_key", external_user_id="other-person")
            assert await call("GET", root + "/domains") == []
            await call("POST", root + "/preview", 404, json=payload)
            principal["external_user_id"] = ""
            await call("GET", root + "/domains", 400)
            principal["auth_type"] = "jwt"
            for model in models + [schema]:
                await call("DELETE", root + "/models/" + model["id"])
            for item in (source, bad, txt):
                await call("DELETE", files_path + "/" + item["id"])
            await call("DELETE", root + "/domains/" + domain["id"])
    await engine.dispose()
    print("PASS: five layers, persistence, all-row types/nulls/composite keys, invalid-save guard, revisions, references and ownership")


if __name__ == "__main__":
    check_validation_and_migration()
    asyncio.run(main())
