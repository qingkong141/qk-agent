"""Saved, authenticated, model-backed offline SQL queries."""
import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.datasets import NameInput, identity, scope, storage
from app.api.modeling import commit, owned_entry
from app.dependencies import CurrentUser, DbSession
from app.models.data_model import DataModel, ThemeDomain
from app.models.dataset import DatasetFile
from app.models.offline_query import OfflineQuery

router = APIRouter(prefix="/studio/offline", tags=["offline"])
slots = asyncio.Semaphore(2)


class QueryInput(BaseModel):
    domain_id: str
    model_ids: list[str] = Field(min_length=1, max_length=10)
    sql: str = Field(min_length=1, max_length=20000)
    row_limit: int = Field(default=1000, ge=1, le=5000)

    @field_validator("model_ids")
    @classmethod
    def distinct_models(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("不能重复选择模型")
        return value

    @field_validator("sql")
    @classmethod
    def nonempty_sql(cls, value):
        if not value.strip():
            raise ValueError("请填写SQL查询")
        return value.strip()


class SavedInput(QueryInput, NameInput):
    expected_revision: int | None = Field(default=None, ge=1)


def info(item):
    updated = item.updated_at
    return {"id":item.id, "name":item.name, "domain_id":item.domain_id, "model_ids":item.model_ids,
            "sql":item.sql, "row_limit":item.row_limit, "revision":item.revision,
            "updated_at":updated.replace(tzinfo=timezone.utc) if updated.tzinfo is None else updated}


async def sources_for(data, db, user):
    await owned_entry(ThemeDomain, data.domain_id, db, user)
    sources = []
    size = 0
    for model_id in data.model_ids:
        model = await owned_entry(DataModel, model_id, db, user)
        if model.domain_id != data.domain_id:
            raise HTTPException(400, "请选择同一主题域下的模型")
        if not model.source_file_id:
            raise HTTPException(400, f"“{model.name}”尚未绑定数据文件，请先在模型配置中绑定")
        file = await owned_entry(DatasetFile, model.source_file_id, db, user)
        if file.modality not in ("structured", "sensor"):
            raise HTTPException(400, "模型需要绑定结构化或传感器数据文件")
        size += file.size
        if size > 25 * 1024 * 1024:
            raise HTTPException(413, "本次查询的模型源文件合计不能超过25MB")
        sources.append({"id":model.id, "name":model.name, "table_name":model.table_name,
                        "fields":model.fields, "revision":model.revision, "path":str(storage(file.id)),
                        "suffix":Path(file.name).suffix.lower(), "sha256":file.sha256})
    return sources


def run_worker(job):
    try:
        process = subprocess.run([sys.executable, "-m", "app.services.offline_worker"],
                                 input=json.dumps(job, ensure_ascii=True), capture_output=True, text=True,
                                 encoding="utf-8", timeout=15, cwd=Path(__file__).resolve().parents[2],
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "查询执行超时，请增加筛选条件或减少关联数据")
    if process.returncode:
        raise HTTPException(500, "查询进程未能完成，请稍后重试")
    try:
        result = json.loads(process.stdout)
    except ValueError:
        raise HTTPException(500, "查询结果无法读取，请稍后重试")
    if "detail" in result:
        raise HTTPException(result.get("status", 500), result["detail"])
    return result["result"]


async def execute(data, db, user):
    sources = await sources_for(data, db, user)
    return await execute_job(sources, sql=data.sql, row_limit=data.row_limit)


async def execute_job(sources, **options):
    try:
        await asyncio.wait_for(slots.acquire(), timeout=2)
    except TimeoutError:
        raise HTTPException(429, "查询任务繁忙，请稍后重试")
    try:
        return await run_in_threadpool(run_worker, {**options, "sources":sources})
    finally:
        slots.release()


@router.post("/run")
async def run(data: QueryInput, db: DbSession, user: CurrentUser):
    return await execute(data, db, user)


@router.get("/queries")
async def queries(db: DbSession, user: CurrentUser):
    return [info(item) for item in await db.scalars(select(OfflineQuery).where(*scope(OfflineQuery, user)).order_by(OfflineQuery.updated_at.desc()))]


async def save(data, db, user, item_id=None):
    if item_id:
        item = await owned_entry(OfflineQuery, item_id, db, user)
        if item.revision != data.expected_revision:
            raise HTTPException(409, "查询已被修改，请重新打开后编辑")
    result = await execute(data, db, user)
    await owned_entry(ThemeDomain, data.domain_id, db, user, lock=True)
    values = data.model_dump(exclude={"expected_revision"})
    if item_id:
        item = await owned_entry(OfflineQuery, item_id, db, user, lock=True)
        await db.refresh(item)
        if item.revision != data.expected_revision:
            raise HTTPException(409, "查询已被修改，请重新打开后编辑")
        for key, value in values.items():
            setattr(item, key, value)
        item.revision += 1
    else:
        item = OfflineQuery(id=str(uuid.uuid4()), **identity(user), **values)
        db.add(item)
    await commit(db)
    await db.refresh(item)
    return {**info(item), "result":result}


@router.post("/queries", status_code=201)
async def add_query(data: SavedInput, db: DbSession, user: CurrentUser):
    return await save(data, db, user)


@router.get("/queries/{item_id}")
async def get_query(item_id: str, db: DbSession, user: CurrentUser):
    return info(await owned_entry(OfflineQuery, item_id, db, user))


@router.put("/queries/{item_id}")
async def edit_query(item_id: str, data: SavedInput, db: DbSession, user: CurrentUser):
    return await save(data, db, user, item_id)


@router.delete("/queries/{item_id}")
async def remove_query(item_id: str, db: DbSession, user: CurrentUser):
    item = await owned_entry(OfflineQuery, item_id, db, user, lock=True)
    await db.delete(item)
    await db.commit()
    return {"id":item_id}
