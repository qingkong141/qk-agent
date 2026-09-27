"""Authenticated API access to explicitly saved pipeline output snapshots."""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field, JsonValue, model_validator
from sqlalchemy import delete, select, update

from app.api.datasets import NameInput, identity, scope
from app.api.studio import RevisionInput
from app.dependencies import CurrentUser, DbSession
from app.models.studio import StudioArtifact

router = APIRouter(prefix="/studio/data-services", tags=["data-services"])
KIND = "data_service"


class SettingsInput(NameInput, RevisionInput):
    description: str = Field(default="", max_length=1000)
    enabled: bool


class ResultInput(NameInput):
    description: str = Field(default="", max_length=1000)
    pipeline_id: str
    pipeline_revision: int = Field(ge=1)
    rows: list[dict[str, JsonValue]] = Field(max_length=10000)
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def bounded_result(self):
        fields = {key for row in self.rows for key in row}
        if len(fields) > 200 or any(not key.strip() or len(key) > 256 for key in fields):
            raise ValueError("最多200个字段，字段名需为1到256字的非空文本")
        try:
            size = len(json.dumps(self.rows, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (ValueError, RecursionError):
            raise ValueError("结果必须是有限数值及合法JSON数据")
        if size > 2 * 1024 * 1024:
            raise ValueError("单次开放结果不能超过2MB")
        return self


def info(item):
    config = item.config
    return {"id": item.id, "name": item.name, "revision": item.revision,
            **{key: config[key] for key in ("description", "enabled", "pipeline_id", "pipeline_name", "pipeline_revision", "captured_at", "fields")},
            "row_count": len(config["rows"]), "updated_at": item.updated_at.replace(tzinfo=timezone.utc) if item.updated_at.tzinfo is None else item.updated_at,
            "path": f"/studio/data-services/{item.id}/data"}


async def owned(item_id, db, user):
    item = await db.scalar(select(StudioArtifact).where(StudioArtifact.id == item_id, StudioArtifact.kind == KIND, *scope(StudioArtifact, user)))
    if not item:
        raise HTTPException(404, "数据服务不存在")
    return item


async def snapshot(data, db, user):
    pipeline = await db.scalar(select(StudioArtifact).where(StudioArtifact.id == data.pipeline_id, StudioArtifact.kind == "pipeline", *scope(StudioArtifact, user)))
    if not pipeline:
        raise HTTPException(404, "来源管道不存在")
    if pipeline.revision != data.pipeline_revision:
        raise HTTPException(409, "来源管道已更新，请重新打开、运行并保存后再开放结果")
    return {"description": data.description, "pipeline_id": pipeline.id, "pipeline_name": pipeline.name,
            "pipeline_revision": pipeline.revision, "captured_at": datetime.now(timezone.utc).isoformat(),
            "fields": list(dict.fromkeys(key for row in data.rows for key in row)), "rows": data.rows}


async def change(item, revision, values, db, user):
    changed = await db.execute(update(StudioArtifact).where(StudioArtifact.id == item.id, StudioArtifact.kind == KIND,
        StudioArtifact.revision == revision, *scope(StudioArtifact, user)).values(**values, revision=StudioArtifact.revision + 1))
    if changed.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "数据服务已更新，请刷新后重试")
    await db.commit()
    await db.refresh(item)
    return info(item)


@router.get("")
async def listing(db: DbSession, user: CurrentUser):
    return [info(item) for item in await db.scalars(select(StudioArtifact).where(StudioArtifact.kind == KIND, *scope(StudioArtifact, user)).order_by(StudioArtifact.updated_at.desc()))]


@router.post("", status_code=201)
async def create(data: ResultInput, db: DbSession, user: CurrentUser):
    config = await snapshot(data, db, user)
    item = StudioArtifact(id=str(uuid.uuid4()), name=data.name, kind=KIND, config={**config, "enabled": True}, **identity(user))
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return info(item)


@router.get("/{item_id}")
async def detail(item_id: str, db: DbSession, user: CurrentUser):
    return info(await owned(item_id, db, user))


@router.put("/{item_id}")
async def configure(item_id: str, data: SettingsInput, db: DbSession, user: CurrentUser):
    item = await owned(item_id, db, user)
    return await change(item, data.expected_revision, {"name": data.name, "config": {**item.config, "description": data.description, "enabled": data.enabled}}, db, user)


@router.put("/{item_id}/result")
async def replace_result(item_id: str, data: ResultInput, db: DbSession, user: CurrentUser):
    item = await owned(item_id, db, user)
    if item.config["pipeline_id"] != data.pipeline_id:
        raise HTTPException(400, "只能更新同一来源管道的结果")
    config = await snapshot(data, db, user)
    return await change(item, data.expected_revision, {"name": data.name, "config": {**config, "enabled": item.config["enabled"]}}, db, user)


@router.delete("/{item_id}")
async def remove(item_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    await owned(item_id, db, user)
    changed = await db.execute(delete(StudioArtifact).where(StudioArtifact.id == item_id, StudioArtifact.kind == KIND,
        StudioArtifact.revision == data.expected_revision, *scope(StudioArtifact, user)))
    if changed.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "数据服务已更新，请刷新后重试")
    await db.commit()
    return {"id": item_id}


@router.get("/{item_id}/data")
async def read_data(item_id: str, db: DbSession, user: CurrentUser, response: Response,
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=500)):
    item = await owned(item_id, db, user)
    if not item.config["enabled"]:
        raise HTTPException(409, "数据服务已停用")
    response.headers["Cache-Control"] = "no-store"
    rows = item.config["rows"]
    start = (page - 1) * page_size
    return {"service_id": item.id, "revision": item.revision, "captured_at": item.config["captured_at"],
            "pipeline_revision": item.config["pipeline_revision"], "fields": item.config["fields"],
            "page": page, "page_size": page_size, "total": len(rows), "rows": rows[start:start + page_size]}
