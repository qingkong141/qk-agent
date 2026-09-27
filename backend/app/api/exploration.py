"""Time-window aggregation over owned models with saved exploration configurations."""
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update

from app.api.datasets import identity, scope, NameInput
from app.api.offline import sources_for, execute_job
from app.dependencies import CurrentUser, DbSession
from app.models.studio import StudioArtifact

router = APIRouter(prefix="/studio/exploration", tags=["exploration"])
ZONE = timezone(timedelta(hours=8))


class Series(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    model_id: str
    time_field: str
    value_field: str = ""
    aggregate: Literal["avg", "sum", "min", "max", "count"] = "avg"
    filter_field: str = ""
    filter_value: str = Field(default="", max_length=255)


class Exploration(BaseModel):
    domain_id: str
    start: datetime
    end: datetime
    interval_seconds: int = Field(ge=60, le=86400)
    chart: Literal["line", "bar"] = "line"
    series: list[Series] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_window(self):
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=ZONE)
        if self.end.tzinfo is None:
            self.end = self.end.replace(tzinfo=ZONE)
        self.start = self.start.astimezone(ZONE)
        self.end = self.end.astimezone(ZONE)
        duration = (self.end - self.start).total_seconds()
        if duration <= 0:
            raise ValueError("结束时间必须晚于开始时间")
        if math.ceil(duration / self.interval_seconds) > 500:
            raise ValueError("最多500个时间段，请缩短时间范围或增大聚合间隔")
        names = [series.name.strip() for series in self.series]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("指标名称不能为空或重复")
        for series, name in zip(self.series, names):
            series.name = name
        return self

    @property
    def model_ids(self):
        return list(dict.fromkeys(series.model_id for series in self.series))


class SavedInput(NameInput):
    config: Exploration
    expected_revision: int | None = Field(default=None, ge=1)


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def query_for(data, sources):
    models = {source["id"]: source for source in sources}
    parts = []
    params = {"start": data.start.timestamp(), "end": data.end.timestamp(), "interval": data.interval_seconds}
    for index, series in enumerate(data.series):
        model = models[series.model_id]
        fields = {field["name"]: field for field in model["fields"]}
        if fields.get(series.time_field, {}).get("type") != "datetime":
            raise HTTPException(400, f"{series.name}：请选择日期时间类型的时间字段")
        value = "*"
        if series.aggregate != "count":
            if fields.get(series.value_field, {}).get("type") not in ("integer", "number"):
                raise HTTPException(400, f"{series.name}：请选择数值类型的指标字段")
            value = quote(series.value_field)
        timestamp = f"studio_epoch({quote(series.time_field)})"
        condition = f"{timestamp} >= :start AND {timestamp} < :end"
        if series.filter_field:
            if fields.get(series.filter_field, {}).get("type") != "string":
                raise HTTPException(400, f"{series.name}：筛选字段请选择文本类型，如设备编号或科室")
            condition += f" AND {quote(series.filter_field)} = :filter_{index}"
            params[f"filter_{index}"] = series.filter_value
        parts.append(f"SELECT {index} AS series, CAST(({timestamp} - :start) / :interval AS INTEGER) AS bucket, "
                     f"{series.aggregate.upper()}({value}) AS value, COUNT(*) AS samples "
                     f"FROM {quote(model['table_name'])} WHERE {condition} GROUP BY bucket")
    return " UNION ALL ".join(parts) + " ORDER BY series, bucket", params


async def execute(data, db, user):
    sources = await sources_for(data, db, user)
    sql, params = query_for(data, sources)
    result = await execute_job(sources, sql=sql, params=params, exploration=True, row_limit=3000)
    if result["truncated"] or result["large_integer_columns"]:
        raise HTTPException(400, "聚合结果超出完整展示或安全数值范围，请减少指标或数据范围")
    count = math.ceil((data.end - data.start).total_seconds() / data.interval_seconds)
    series = [{"name": item.name, "values": [None] * count, "samples": [0] * count} for item in data.series]
    for index, bucket, value, samples in result["rows"]:
        series[index]["values"][bucket] = value
        series[index]["samples"][bucket] = samples
    return {"times": [(data.start + timedelta(seconds=i * data.interval_seconds)).astimezone(ZONE).isoformat() for i in range(count)],
            "series": series, "sources": result["sources"], "elapsed_ms": result["elapsed_ms"]}


def info(item):
    return {"id": item.id, "name": item.name, "config": item.config, "revision": item.revision}


async def owned(item_id, db, user):
    item = await db.scalar(select(StudioArtifact).where(StudioArtifact.id == item_id, StudioArtifact.kind == "exploration", *scope(StudioArtifact, user)))
    if not item:
        raise HTTPException(404, "数据探索配置不存在")
    return item


@router.post("/run")
async def run(data: Exploration, db: DbSession, user: CurrentUser):
    return await execute(data, db, user)


@router.get("/configs")
async def configs(db: DbSession, user: CurrentUser):
    return [info(item) for item in await db.scalars(select(StudioArtifact).where(StudioArtifact.kind == "exploration", *scope(StudioArtifact, user)).order_by(StudioArtifact.updated_at.desc()))]


async def save(data, db, user, item_id=None):
    if item_id:
        item = await owned(item_id, db, user)
        if item.revision != data.expected_revision:
            raise HTTPException(409, "配置已修改，请重新打开后编辑")
    result = await execute(data.config, db, user)
    values = {"name": data.name, "config": data.config.model_dump(mode="json")}
    if item_id:
        updated = await db.execute(update(StudioArtifact).where(StudioArtifact.id == item_id, *scope(StudioArtifact, user),
            StudioArtifact.revision == data.expected_revision).values(**values, revision=StudioArtifact.revision + 1))
        if updated.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, "配置已修改，请重新打开后编辑")
    else:
        item = StudioArtifact(id=str(uuid.uuid4()), kind="exploration", **identity(user), **values)
        db.add(item)
    await db.commit()
    await db.refresh(item)
    return {**info(item), "result": result}


@router.post("/configs", status_code=201)
async def create(data: SavedInput, db: DbSession, user: CurrentUser):
    return await save(data, db, user)


@router.get("/configs/{item_id}")
async def get(item_id: str, db: DbSession, user: CurrentUser):
    return info(await owned(item_id, db, user))


@router.put("/configs/{item_id}")
async def edit(item_id: str, data: SavedInput, db: DbSession, user: CurrentUser):
    return await save(data, db, user, item_id)


@router.delete("/configs/{item_id}")
async def remove(item_id: str, db: DbSession, user: CurrentUser):
    item = await owned(item_id, db, user)
    await db.delete(item)
    await db.commit()
    return {"id": item_id}
