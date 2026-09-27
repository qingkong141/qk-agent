"""Isolated catalog, storage, parsing, ownership and deletion regressions."""
import asyncio
import base64
import hashlib
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import datasets
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.dataset import Dataset, DatasetFile, DatasetFolder
from app.models.data_model import DataModel, ThemeDomain


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    with tempfile.TemporaryDirectory() as directory:
        datasets.settings.DATASET_DIR = directory
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def call(method, path, status=200, **kwargs):
                response = await client.request(method, "/datasets" + path, **kwargs)
                assert response.status_code == status, (method, path, response.status_code, response.text)
                return response

            await call("POST", "/folders", 422, json={"name": "  "})
            folder = (await call("POST", "/folders", 201, json={"name": "设备工况"})).json()
            await call("POST", "/folders", 409, json={"name": "设备工况"})
            payload = {"folder_id": folder["id"], "name": "输液数据", "description": "处理结果"}
            item = (await call("POST", "", 201, json=payload)).json()
            path = "/" + item["id"]
            await call("POST", "", 409, json=payload)
            await call("DELETE", "/folders/" + folder["id"], 409)
            assert len((await call("GET", "?q=输液")).json()) == 1
            assert (await call("GET", "?q=%25")).json() == []
            png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=")
            fixtures = [("structured", "report.json", '[{"deviceId":"INF-001","battery":61},{"deviceId":"INF-002","battery":0}]'.encode()),
                        ("sensor", "sensor.csv", b'deviceId,battery\nINF-001,61\nINF-002,0\n'),
                        ("text", "note.txt", "第一行\n设备温度正常\n<script>never execute</script>".encode()),
                        ("image", "photo.png", png), ("video", "video.mp4", b'\x00\x00\x00\x18ftypmp42' + bytes(20))]
            files = []
            for modality, name, data in fixtures:
                response = await call("POST", path + "/files", 201, data={"modality": modality}, files={"file": (name, data)})
                entry = response.json(); files.append(entry)
                assert entry["sha256"] == hashlib.sha256(data).hexdigest()
                downloaded = await call("GET", path + "/files/" + entry["id"] + "/content")
                assert downloaded.content == data
                assert downloaded.headers["content-disposition"].startswith("attachment")
            rows = (await call("GET", path + "/files/" + files[0]["id"] + "/preview?q=INF-002&page_size=1")).json()
            assert rows["total"] == 1 and rows["rows"][0]["battery"] == 0
            text = (await call("GET", path + "/files/" + files[2]["id"] + "/preview?q=温度")).json()
            assert text["total"] == 1 and text["text"] == "设备温度正常"
            assert len((await call("GET", path + "/files?modality=sensor")).json()) == 1
            await call("DELETE", path, 409)
            for name, body, kind in [("bad.json", b'{bad}', "structured"), ("obj.json", b'{}', "sensor"),
                                     ("infinite.json", b'[{"x":1e500}]', "structured"),
                                     ("bad.csv", b'a,a\n1,2', "structured"), ("bad.csv", b'a,b\n1', "structured"),
                                     ("bad.txt", b'\xff\x00', "text"), ("bad.png", b'<html>no</html>', "image"),
                                     ("image.svg", b'<svg/>', "image"), ("empty.txt", b'', "text")]:
                await call("POST", path + "/files", 400, data={"modality": kind}, files={"file": (name, body)})
            previous = datasets.MAX_DATA
            datasets.MAX_DATA = 8
            await call("POST", path + "/files", 413, data={"modality": "text"}, files={"file": ("large.txt", b'123456789')})
            datasets.MAX_DATA = previous
            assert len(list(Path(directory).iterdir())) == 5
            for other in [{"id": "other", "auth_type": "jwt", "external_user_id": ""}, {"id": "owner", "auth_type": "api_key", "external_user_id": "other-person"}]:
                principal.clear(); principal.update(other)
                assert (await call("GET", "")).json() == []
                assert (await call("GET", "/folders")).json() == []
                await call("GET", path, 404)
                await call("POST", "", 404, json=payload)
                await call("GET", path + "/files/" + files[0]["id"] + "/content", 404)
                await call("DELETE", path + "/files/" + files[0]["id"], 404)
            principal.update(id="owner", auth_type="api_key", external_user_id="")
            await call("GET", "", 400)
            principal.update(auth_type="jwt")
            other_folder = (await call("POST", "/folders", 201, json={"name": "归档"})).json()
            await call("PUT", path, json={**payload, "folder_id": other_folder["id"], "name": "归档数据"})
            await call("DELETE", "/folders/" + folder["id"])
            for entry in files:
                await call("DELETE", path + "/files/" + entry["id"])
                await call("GET", path + "/files/" + entry["id"] + "/content", 404)
            assert not list(Path(directory).iterdir())
            await call("DELETE", path)
            await call("DELETE", "/folders/" + other_folder["id"])
            assert (await call("GET", "")).json() == []
    await engine.dispose()
    print("PASS: catalog CRUD, 5 modalities, exact downloads, content queries, invalid/oversize files, account isolation and disk deletion")


asyncio.run(main())
