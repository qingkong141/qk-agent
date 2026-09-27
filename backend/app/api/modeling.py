"""Theme domains and logical schemas backed by private dataset files."""
import json
import math
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.api.datasets import NameInput, identity, owned, read_rows, scope, storage
from app.dependencies import CurrentUser, DbSession
from app.models.data_model import DataModel, ThemeDomain
from app.models.dataset import DatasetFile
from app.models.offline_query import OfflineQuery

router = APIRouter(prefix="/studio/modeling", tags=["modeling"])
Identifier = r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"


class DomainInput(NameInput):
    description: str = Field(default="", max_length=1000)


class ModelField(BaseModel):
    name: str = Field(pattern=Identifier)
    label: str = Field(default="", max_length=120)
    type: Literal["string", "integer", "number", "boolean", "datetime", "json"] = "string"
    role: Literal["attribute", "dimension", "measure"] = "attribute"
    source: str = Field(default="", max_length=500)
    nullable: bool = True
    primary_key: bool = False

    @model_validator(mode="after")
    def key_constraint(self):
        if self.primary_key and (self.nullable or self.type == "json"):
            raise ValueError("主键字段须非空且不能为JSON类型")
        if self.role == "measure" and self.type not in ("number", "integer"):
            raise ValueError("度量字段须使用整数或数值类型")
        return self


class ModelInput(DomainInput):
    domain_id: str
    table_name: str = Field(pattern=Identifier)
    layer: Literal["ODS", "DWD", "DIM", "DWS", "ADS"]
    fields: list[ModelField] = Field(min_length=1, max_length=200)
    source_file_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def distinct_fields(self):
        if len({field.name.lower() for field in self.fields}) != len(self.fields):
            raise ValueError("字段标识不能重复（不区分大小写）")
        self.table_name = self.table_name.lower()
        return self


async def owned_entry(cls, item_id, db, user, lock=False):
    try:
        return await owned(cls, item_id, db, user, lock=lock)
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(404, "主题域、模型、查询或数据文件不存在")
        raise


async def commit(db):
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "名称或表标识已存在，请使用其他名称")


def info(item):
    result = {"id": item.id, "name": item.name, "description": item.description}
    if isinstance(item, DataModel):
        updated = item.updated_at
        result.update(domain_id=item.domain_id, table_name=item.table_name, layer=item.layer,
                      fields=item.fields, source_file_id=item.source_file_id, revision=item.revision,
                      updated_at=updated.replace(tzinfo=timezone.utc) if updated.tzinfo is None else updated)
    return result


def load_rows(item):
    if item.modality not in ("structured", "sensor"):
        raise HTTPException(400, "模型只支持结构化或传感器数据文件")
    try:
        return read_rows(storage(item.id).read_bytes(), Path(item.name).suffix.lower())
    except FileNotFoundError:
        raise HTTPException(410, "源文件内容缺失，请重新上传并绑定")


def convert(value, field):
    if value is None or (value == "" and field.type != "string"):
        if not field.nullable:
            raise ValueError("不能为空")
        return None
    if field.type == "json":
        return value
    if field.type == "string":
        result = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if field.primary_key and not result.strip():
            raise ValueError("主键不能为空")
        return result
    if field.type in ("integer", "number"):
        if isinstance(value, (bool, dict, list)):
            raise ValueError("需要数值")
        try:
            number = Decimal(str(value))
            if not number.is_finite():
                raise ValueError("需要有限数值")
            if field.type == "integer":
                if number != number.to_integral_value() or abs(number) > 9007199254740991:
                    raise ValueError("需要安全范围内的整数，长编号请使用文本")
                return int(number)
            result = float(number)
            if not math.isfinite(result):
                raise ValueError("数值超出范围")
            return result
        except InvalidOperation:
            raise ValueError("需要数值")
    if field.type == "boolean":
        if value is True or value is False:
            return value
        if str(value).lower() in ("true", "1", "false", "0"):
            return str(value).lower() in ("true", "1")
        raise ValueError("需要true / false或1 / 0")
    if not isinstance(value, str):
        raise ValueError("需要ISO日期或时间文本")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        raise ValueError("需要ISO日期或时间文本")


def evaluate(rows, fields):
    columns = {key for row in rows for key in row}
    unknown = [field.source for field in fields if field.source and field.source not in columns]
    if rows and unknown:
        raise HTTPException(400, f"源文件中不存在字段：{', '.join(unknown[:5])}")
    errors, preview, keys, invalid_rows = [], [], set(), 0
    primary = [field.name for field in fields if field.primary_key]
    for index, row in enumerate(rows, 1):
        result, issues = {}, []
        for field in fields:
            try:
                result[field.name] = convert(row.get(field.source) if field.source else None, field)
            except (ValueError, TypeError) as error:
                result[field.name] = None
                issues.append(f"{field.label or field.name}：{error}")
        if primary and not issues:
            key = tuple(result[name] for name in primary)
            if key in keys:
                issues.append("主键重复（多个主键字段按组合校验）")
            keys.add(key)
        if issues:
            invalid_rows += 1
            if len(errors) < 20:
                errors.append({"row": index, "message": "；".join(issues)})
        if len(preview) < 50:
            preview.append(result)
    return {"ok": invalid_rows == 0, "total": len(rows), "invalid_rows": invalid_rows,
            "rows": preview, "errors": errors, "columns": [field.name for field in fields]}


def infer(rows):
    fields, used = [], set()
    for index, key in enumerate(dict.fromkeys(key for row in rows for key in row), 1):
        if len(key) > 500:
            raise HTTPException(400, "源字段名称超过500字，请先在数据管道中重命名")
        name = key if re.fullmatch(Identifier, key) else f"field_{index}"
        suffix = 1
        while name.lower() in used:
            name = f"field_{index}_{suffix}"
            suffix += 1
        used.add(name.lower())
        values = [row[key] for row in rows if key in row and row[key] is not None and row[key] != ""]
        kind = "string"
        if values and all(isinstance(value, bool) for value in values):
            kind = "boolean"
        elif values and any(isinstance(value, (list, dict)) for value in values):
            kind = "json"
        elif any(isinstance(value, int) and abs(value) > 9007199254740991 for value in values):
            kind = "string"
        elif values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            kind = "integer" if all(float(value).is_integer() and abs(value) <= 9007199254740991 for value in values) else "number"
        fields.append(ModelField(name=name, label=key[:120], source=key, type=kind).model_dump())
    return fields


@router.get("/domains")
async def domains(db: DbSession, user: CurrentUser):
    return [info(item) for item in await db.scalars(select(ThemeDomain).where(*scope(ThemeDomain, user)).order_by(ThemeDomain.created_at))]


@router.post("/domains", status_code=201)
async def add_domain(data: DomainInput, db: DbSession, user: CurrentUser):
    scope(ThemeDomain, user)
    item = ThemeDomain(id=str(uuid.uuid4()), **identity(user), **data.model_dump())
    db.add(item)
    await commit(db)
    return info(item)


@router.put("/domains/{item_id}")
async def edit_domain(item_id: str, data: DomainInput, db: DbSession, user: CurrentUser):
    item = await owned_entry(ThemeDomain, item_id, db, user, lock=True)
    item.name, item.description = data.name, data.description
    await commit(db)
    return info(item)


@router.delete("/domains/{item_id}")
async def remove_domain(item_id: str, db: DbSession, user: CurrentUser):
    item = await owned_entry(ThemeDomain, item_id, db, user, lock=True)
    if await db.scalar(select(DataModel.id).where(DataModel.domain_id == item_id).limit(1)):
        raise HTTPException(409, "主题域下还有模型，请先移动或删除模型")
    if await db.scalar(select(OfflineQuery.id).where(OfflineQuery.domain_id == item_id).limit(1)):
        raise HTTPException(409, "主题域下还有离线查询，请先移动或删除查询")
    await db.delete(item)
    await db.commit()
    return {"id": item_id}


@router.get("/models")
async def models(db: DbSession, user: CurrentUser):
    return [info(item) for item in await db.scalars(select(DataModel).where(*scope(DataModel, user)).order_by(DataModel.updated_at.desc()))]


@router.get("/sources/{file_id}")
async def source(file_id: str, db: DbSession, user: CurrentUser):
    item = await owned_entry(DatasetFile, file_id, db, user)
    rows = await run_in_threadpool(load_rows, item)
    return {"fields": await run_in_threadpool(infer, rows), "total": len(rows), "name": item.name, "dataset_id": item.dataset_id}


async def validate_source(data, db, user, lock=False):
    await owned_entry(ThemeDomain, data.domain_id, db, user, lock=lock)
    if not data.source_file_id:
        return {"ok": True, "total": 0, "invalid_rows": 0, "rows": [], "errors": [],
                "columns": [field.name for field in data.fields], "bound": False}
    item = await owned_entry(DatasetFile, data.source_file_id, db, user, lock=lock)
    rows = await run_in_threadpool(load_rows, item)
    return {**await run_in_threadpool(evaluate, rows, data.fields), "bound": True}


@router.post("/preview")
async def preview(data: ModelInput, db: DbSession, user: CurrentUser):
    return await validate_source(data, db, user)


async def save(data, db, user, item_id=None):
    result = await validate_source(data, db, user, lock=True)
    if not result["ok"]:
        first = result["errors"][0]
        raise HTTPException(422, f"数据校验未通过：{result['invalid_rows']}条异常。第{first['row']}条：{first['message']}")
    values = data.model_dump(exclude={"expected_revision"})
    if item_id:
        item = await owned_entry(DataModel, item_id, db, user, lock=True)
        if data.expected_revision != item.revision:
            raise HTTPException(409, "模型已被修改，请重新打开后编辑")
        for key, value in values.items():
            setattr(item, key, value)
        item.revision += 1
    else:
        item = DataModel(id=str(uuid.uuid4()), **identity(user), **values)
        db.add(item)
    await commit(db)
    await db.refresh(item)
    return info(item)


@router.post("/models", status_code=201)
async def add_model(data: ModelInput, db: DbSession, user: CurrentUser):
    return await save(data, db, user)


@router.get("/models/{item_id}")
async def get_model(item_id: str, db: DbSession, user: CurrentUser):
    return info(await owned_entry(DataModel, item_id, db, user))


@router.put("/models/{item_id}")
async def edit_model(item_id: str, data: ModelInput, db: DbSession, user: CurrentUser):
    return await save(data, db, user, item_id)


@router.delete("/models/{item_id}")
async def remove_model(item_id: str, db: DbSession, user: CurrentUser):
    item = await owned_entry(DataModel, item_id, db, user, lock=True)
    await db.delete(item)
    await db.commit()
    return {"id": item_id}
