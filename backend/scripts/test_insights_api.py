"""Deterministic model responses, real SQL workers, isolated metadata and files."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import datasets, modeling, insights, studio
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.dataset import DatasetFolder, Dataset, DatasetFile
from app.models.data_model import ThemeDomain, DataModel
from app.models.offline_query import OfflineQuery
from app.models.studio import StudioArtifact


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for cls in (DatasetFolder, Dataset, DatasetFile, ThemeDomain, DataModel, OfflineQuery, StudioArtifact):
            await connection.run_sync(cls.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {"id":"owner", "auth_type":"jwt", "external_user_id":""}
    async def db_override():
        async with sessions() as session:
            yield session
    async def user_override():
        return principal
    app = FastAPI()
    for module in (datasets, modeling, insights, studio):
        app.include_router(module.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    contexts = []
    async def respond(messages):
        data = json.loads(messages[1].content)
        contexts.append(data)
        assert "ROW_ONLY_SECRET" not in messages[1].content
        assert all("path" not in model and "owner_id" not in model and "id" not in model for model in data["catalog"])
        question = data["question"]
        if question == "timeout":
            raise asyncio.TimeoutError()
        if question == "offline":
            raise RuntimeError("provider secret must not be exposed")
        if question == "malformed":
            return SimpleNamespace(content="not json")
        plan = {"action":"query", "explanation":"按设备科室关联全部记录，计算各科室非空电量的平均值。", "tables":["readings","devices"],
                "sql":'SELECT d.ward AS "科室", AVG(r.battery) AS "平均电量" FROM readings r LEFT JOIN devices d ON r.device_id=d.device_id GROUP BY d.ward ORDER BY "平均电量" DESC',
                "chart":{"kind":"bar","x":"科室","y":["平均电量"]}}
        if question == "clarify":
            plan = {"action":"clarify","explanation":"请说明要查询哪项指标和时间范围。"}
        elif question == "followup":
            assert data["history"] and data["history"][-1]["question"] == "first"
            plan["sql"] += " LIMIT 1"
        elif question == "unknown":
            plan["tables"] = ["private_table"]
        elif question == "write":
            plan["sql"] = "DELETE FROM readings"
        elif question == "system":
            plan["sql"] = "SELECT * FROM sqlite_master"
        elif question == "field":
            plan["sql"] = "SELECT missing_field FROM readings"
        elif question == "bad_chart":
            plan["chart"]["y"] = ["科室"]
        elif question == "duplicate_columns":
            plan["sql"] = 'SELECT device_id AS "科室", battery AS "平均电量", battery AS "平均电量" FROM readings'
        elif question == "empty":
            plan["sql"] = 'SELECT device_id AS "科室", battery AS "平均电量" FROM readings WHERE 1=0'
        elif question == "cap":
            plan["sql"] = 'SELECT a.device_id AS "科室", a.battery AS "平均电量" FROM readings a, readings b, readings c, readings d, readings e'
        elif question == "changed":
            async with sessions() as session:
                await session.execute(update(DataModel).where(DataModel.table_name == "readings").values(revision=DataModel.revision+1))
                await session.commit()
        return SimpleNamespace(content="```json\n"+json.dumps(plan, ensure_ascii=False)+"\n```")
    insights.create_chat_model = lambda **kwargs: SimpleNamespace(ainvoke=respond)
    with tempfile.TemporaryDirectory() as directory:
        datasets.settings.DATASET_DIR = directory
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def call(method,path,status=200,**kwargs):
                response = await client.request(method,path,**kwargs)
                assert response.status_code == status, (path,response.status_code,response.text)
                assert "provider secret" not in response.text
                return response.json()
            domain = await call("POST","/studio/modeling/domains",201,json={"name":"设备分析"})
            folder = await call("POST","/datasets/folders",201,json={"name":"folder"})
            dataset = await call("POST","/datasets",201,json={"name":"records","folder_id":folder["id"]})
            async def model(name,rows):
                file = await call("POST",f"/datasets/{dataset['id']}/files",201,data={"modality":"structured"},files={"file":(name+".json",json.dumps(rows).encode())})
                return await call("POST","/studio/modeling/models",201,json={"name":name,"domain_id":domain["id"],"table_name":name,"layer":"DWD","fields":modeling.infer(rows),"source_file_id":file["id"]})
            await model("readings",[{"device_id":"d1","battery":0},{"device_id":"d1","battery":20},{"device_id":"d2","battery":40},{"device_id":"ROW_ONLY_SECRET","battery":None}])
            await model("devices",[{"device_id":"d1","ward":"急诊"},{"device_id":"d2","ward":"输液"}])
            async def ask(question,status=200,**extra):
                return await call("POST","/studio/insights/ask",status,json={"domain_id":domain["id"],"question":question,**extra})
            first = await ask("first")
            thread,turn = first["thread"],first["turn"]
            path = "/studio/insights/threads/"+thread["id"]
            assert first["result"]["rows"] == [["输液",40],["急诊",10],[None,None]]
            assert turn["plan"]["chart"]["kind"] == "bar" and thread["revision"] == 1
            followup = await ask("followup",thread_id=thread["id"],expected_revision=1)
            assert followup["result"]["rows"] == [["输液",40]] and followup["thread"]["turn_count"] == 2
            await ask("followup",409,thread_id=thread["id"],expected_revision=1)
            stored = await call("GET",path)
            assert "result" not in stored["turns"][0]
            assert await call("GET","/studio/artifacts") == []
            count = len(contexts)
            assert (await call("POST",path+"/turns/"+turn["id"]+"/run"))["result"]["rows"] == first["result"]["rows"]
            assert len(contexts) == count, "Rerunning uses the saved SQL without another AI call"
            clarify = await ask("clarify",thread_id=thread["id"],expected_revision=2)
            assert clarify["result"] is None and clarify["turn"]["plan"]["action"] == "clarify"
            await call("POST",path+"/turns/"+clarify["turn"]["id"]+"/run",400)
            for question,status in [("unknown",502),("write",400),("system",400),("field",400),("malformed",502),("offline",503),("timeout",504),("changed",409)]:
                await ask(question,status,thread_id=thread["id"],expected_revision=3)
            assert (await call("GET",path))["turn_count"] == 3
            for question in ("bad_chart","duplicate_columns","empty"):
                answer = await ask(question)
                assert answer["turn"]["plan"]["chart"]["kind"] == "table"
            answer = await ask("cap")
            assert answer["result"]["truncated"] and len(answer["result"]["rows"]) == 500
            principal["id"] = "other"
            assert await call("GET","/studio/insights/threads") == []
            await call("GET",path,404)
            await call("DELETE",path,404)
            await call("POST",path+"/turns/"+turn["id"]+"/run",404)
            await ask("first",404)
            principal.update(id="owner",auth_type="api_key",external_user_id="person")
            await ask("first",404)
            principal["external_user_id"] = ""
            await ask("first",400)
            principal["auth_type"] = "jwt"
            await call("DELETE",path)
            await call("GET",path,404)
    await engine.dispose()
    print("PASS: actual SQL, schema-only AI context, followups/clarification, read-only and ownership guards, malformed/provider failures, stale schema/version guards, chart fallback, row limits and saved reruns")


if __name__ == "__main__":
    asyncio.run(main())
