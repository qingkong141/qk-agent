"""Isolated fixtures; browser-created exploration examples are retained separately."""
import asyncio
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import datasets, modeling, exploration
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.dataset import DatasetFolder, Dataset, DatasetFile
from app.models.data_model import ThemeDomain, DataModel
from app.models.studio import StudioArtifact
from app.models.offline_query import OfflineQuery


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for cls in (DatasetFolder, Dataset, DatasetFile, ThemeDomain, DataModel, StudioArtifact, OfflineQuery):
            await connection.run_sync(cls.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {"id": "owner", "auth_type": "jwt", "external_user_id": ""}
    async def db_override():
        async with sessions() as session:
            yield session
    async def user_override():
        return principal
    app = FastAPI()
    for module in (datasets, modeling, exploration):
        app.include_router(module.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    with tempfile.TemporaryDirectory() as directory:
        datasets.settings.DATASET_DIR = directory
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def call(method, path, status=200, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert response.status_code == status, (path, response.status_code, response.text)
                return response.json()
            domain = await call("POST", "/studio/modeling/domains", 201, json={"name": "运行分析"})
            folder = await call("POST", "/datasets/folders", 201, json={"name": "folder"})
            dataset = await call("POST", "/datasets", 201, json={"name": "records", "folder_id": folder["id"]})
            async def model(name, pairs):
                rows = [{"time": time, "battery": value, "device": "D1"} for time, value in pairs]
                file = await call("POST", f"/datasets/{dataset['id']}/files", 201, data={"modality": "structured"}, files={"file": (name+".json", json.dumps(rows).encode())})
                fields = [{"name": name, "label": name, "source": name, "type": kind, "role": "attribute", "nullable": True} for name, kind in [("time", "datetime"), ("battery", "number"), ("device", "string")]]
                return await call("POST", "/studio/modeling/models", 201, json={"name": name, "table_name": name, "domain_id": domain["id"], "layer": "DWD", "fields": fields, "source_file_id": file["id"]})
            a = await model("readings", [("2026-09-27T09:59:59+08:00",999), ("2026-09-27T10:00:00+08:00",0), ("2026-09-27T02:00:30Z",20), ("2026-09-27T10:01:00",None), ("2026-09-27T10:02:00+08:00",40), ("2026-09-27T10:03:00+08:00",999), (None,88)])
            b = await model("comparison", [("2026-09-27T10:00:00",100), ("2026-09-27T10:02:00",60)])
            series = {"name": "平均电量", "model_id": a["id"], "time_field": "time", "value_field": "battery", "aggregate": "avg", "filter_field": "device", "filter_value": "D1"}
            payload = {"domain_id": domain["id"], "start": "2026-09-27T02:00:00Z", "end": "2026-09-27T02:03:00Z", "interval_seconds": 60, "series": [series]}
            async def run(status=200, **changes):
                return await call("POST", "/studio/exploration/run", status, json={**payload, **changes})
            result = await run()
            assert result["series"][0]["values"] == [10,None,40] and result["series"][0]["samples"] == [2,1,1], result
            assert result["times"][0] == "2026-09-27T10:00:00+08:00"
            for aggregate, expected in [("sum",[20,None,40]), ("min",[0,None,40]), ("max",[20,None,40]), ("count",[2,1,1])]:
                assert (await run(series=[{**series,"aggregate":aggregate}]))["series"][0]["values"] == expected
            assert (await run(interval_seconds=120))["series"][0]["values"] == [10,40]
            assert (await run(series=[{**series,"filter_value":"D1' OR 1=1 --"}]))["series"][0]["values"] == [None,None,None]
            result = await run(series=[series,{**series,"name":"另一个模型","model_id":b["id"]}])
            assert result["series"][1]["values"] == [100,None,60] and len(result["sources"]) == 2
            result = await run(series=[series,{**series,"name":"同模型最大值","aggregate":"max"}])
            assert result["series"][1]["values"] == [20,None,40] and len(result["sources"]) == 1
            many = await model("full_input", [("2026-09-27T10:00:00",1)]*120)
            assert (await run(series=[{**series,"model_id":many["id"],"aggregate":"sum"}]))["series"][0]["values"] == [120,None,None]
            await run(422,end=payload["start"])
            await run(422,end="2027-01-01T00:00:00Z")
            await run(422,interval_seconds=0)
            await run(422,series=[])
            await run(422,series=[series,series])
            await run(422,series=[{**series,"aggregate":"randomblob"}])
            await run(400,series=[{**series,"time_field":"device"}])
            await run(400,series=[{**series,"value_field":"device"}])
            await run(400,series=[{**series,"filter_field":"battery"}])
            await run(404,series=[{**series,"model_id":"missing"}])
            saved = await call("POST", "/studio/exploration/configs", 201, json={"name":"电量趋势","config":payload})
            path = "/studio/exploration/configs/"+saved["id"]
            assert saved["revision"] == 1 and saved["config"]["start"] == "2026-09-27T10:00:00+08:00"
            assert (await call("GET",path))["config"] == saved["config"]
            edit = {"name":"趋势更新", "config":{**payload,"chart":"bar"},"expected_revision":1}
            assert (await call("PUT",path,json=edit))["revision"] == 2
            await call("PUT",path,409,json=edit)
            invalid = copy.deepcopy(edit)
            invalid["expected_revision"] = 2
            invalid["config"]["series"][0]["value_field"] = "device"
            await call("PUT",path,400,json=invalid)
            assert (await call("GET",path))["revision"] == 2
            principal["id"] = "other"
            assert await call("GET","/studio/exploration/configs") == []
            await call("GET",path,404)
            await call("PUT",path,404,json=edit)
            await call("DELETE",path,404)
            await run(404)
            principal.update(id="owner",auth_type="api_key",external_user_id="person")
            await run(404)
            principal["external_user_id"] = ""
            await run(400)
            principal["auth_type"] = "jwt"
            await call("DELETE",path)
            await call("GET",path,404)
    await engine.dispose()
    print("PASS: full-input aggregation, intervals/timezones/boundaries, same/cross models, null/zero/gaps, safe filters, limits, saved configs, invalid-save guard, revisions and isolation")


if __name__ == "__main__":
    asyncio.run(main())
