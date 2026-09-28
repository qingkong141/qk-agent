"""Authenticated business catalogs, datasets and private multimodal file storage."""
import csv
import hashlib
import io
import json
import uuid
from datetime import timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.dependencies import CurrentUser, DbSession
from app.models.dataset import Dataset, DatasetFile, DatasetFolder
from app.models.data_model import DataModel

router = APIRouter(prefix="/datasets", tags=["datasets"])
Modality = Literal["structured", "text", "image", "video", "sensor"]
MAX_MEDIA = 50 * 1024 * 1024
MAX_DATA = 10 * 1024 * 1024


class NameInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("名称不能为空或包含控制字符")
        return value


class DatasetInput(NameInput):
    folder_id: str
    description: str = Field(default="", max_length=1000)


def scope(model, user):
    if user.get("auth_type") == "api_key" and not user.get("external_user_id"):
        raise HTTPException(400, "API Key调用需提供X-End-User-ID")
    return model.owner_id == user["id"], model.external_user_id == user.get("external_user_id", "")


def identity(user):
    return dict(owner_id=user["id"], external_user_id=user.get("external_user_id", ""))


async def owned(model, item_id, db, user, lock=False):
    where = (model.id == item_id, *scope(model, user))
    # Taking a write lock before changing children also works with SQLite.
    if lock:
        changed = await db.execute(update(model).where(*where).values(name=model.name))
        if not changed.rowcount:
            raise HTTPException(404, "目录、数据集或文件不存在")
    item = await db.scalar(select(model).where(*where))
    if not item:
        raise HTTPException(404, "目录、数据集或文件不存在")
    return item


async def commit(db):
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "同一目录内名称已存在，请使用其他名称")


def info(item):
    created = item.created_at
    values = {"id": item.id, "name": item.name, "created_at": created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created}
    if isinstance(item, Dataset):
        values.update(folder_id=item.folder_id, description=item.description)
    if isinstance(item, DatasetFile):
        values.update(dataset_id=item.dataset_id, modality=item.modality, mime=item.mime,
                      size=item.size, sha256=item.sha256, row_count=item.row_count)
    return values


def storage(item_id):
    # Storage paths are server-generated UUIDs, never filenames or client paths.
    return Path(settings.DATASET_DIR).resolve() / str(uuid.UUID(item_id))


def read_rows(data: bytes, suffix: str):
    try:
        text = data.decode("utf-8-sig")
        if suffix == ".json":
            def invalid_constant(value):
                raise ValueError(f"不支持 {value}")
            rows = json.loads(text, parse_constant=invalid_constant)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("JSON需要是对象数组")
            # Reject numeric overflow, which standard JSON parsers may convert to Infinity.
            json.dumps(rows, allow_nan=False)
        else:
            reader = csv.DictReader(io.StringIO(text), strict=True)
            if not reader.fieldnames or any(not key.strip() for key in reader.fieldnames) or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("CSV首行需提供非空、不重复的字段名")
            rows = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV每行列数需与表头一致")
                rows.append(row)
                if len(rows) > 50000:
                    raise ValueError("单文件最多50,000条记录")
        if len(rows) > 50000 or len({key for row in rows for key in row}) > 200:
            raise ValueError("单文件最多50,000条记录、200个字段")
        return rows
    except (ValueError, UnicodeError, csv.Error, RecursionError) as error:
        raise HTTPException(400, f"数据文件格式错误：{error}")


def inspect_file(data, suffix, modality):
    if not data:
        raise HTTPException(400, "不能上传空文件")
    if modality in ("structured", "sensor"):
        if suffix not in (".csv", ".json"):
            raise HTTPException(400, "结构化或传感器数据支持CSV、JSON对象数组")
        return ("application/json" if suffix == ".json" else "text/csv"), len(read_rows(data, suffix))
    if modality == "text":
        if suffix not in (".txt", ".md", ".log"):
            raise HTTPException(400, "文本支持TXT、MD、LOG")
        try:
            text = data.decode("utf-8-sig")
            if "\x00" in text:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise HTTPException(400, "文本需使用UTF-8编码")
        return "text/plain", None
    if modality == "image":
        formats = {".png": (data.startswith(b"\x89PNG\r\n\x1a\n"), "image/png"),
                   ".jpg": (data.startswith(b"\xff\xd8\xff"), "image/jpeg"),
                   ".jpeg": (data.startswith(b"\xff\xd8\xff"), "image/jpeg"),
                   ".gif": (data[:6] in (b"GIF87a", b"GIF89a"), "image/gif"),
                   ".webp": (data[:4] == b"RIFF" and data[8:12] == b"WEBP", "image/webp")}
    else:
        formats = {".mp4": (data[4:8] == b"ftyp", "video/mp4"),
                   ".webm": (data[:4] == b"\x1aE\xdf\xa3", "video/webm")}
    valid, mime = formats.get(suffix, (False, ""))
    if not valid:
        raise HTTPException(400, "文件扩展名或内容不符合所选类型")
    return mime, None


@router.get("/folders")
async def folders(db: DbSession, user: CurrentUser):
    items = await db.scalars(select(DatasetFolder).where(*scope(DatasetFolder, user)).order_by(DatasetFolder.created_at))
    return [info(item) for item in items]


@router.post("/folders", status_code=201)
async def add_folder(data: NameInput, db: DbSession, user: CurrentUser):
    scope(DatasetFolder, user)
    item = DatasetFolder(id=str(uuid.uuid4()), **identity(user), name=data.name)
    db.add(item)
    await commit(db)
    await db.refresh(item)
    return info(item)


@router.put("/folders/{item_id}")
async def edit_folder(item_id: str, data: NameInput, db: DbSession, user: CurrentUser):
    item = await owned(DatasetFolder, item_id, db, user, lock=True)
    item.name = data.name
    await commit(db)
    return info(item)


@router.delete("/folders/{item_id}")
async def delete_folder(item_id: str, db: DbSession, user: CurrentUser):
    item = await owned(DatasetFolder, item_id, db, user, lock=True)
    if await db.scalar(select(Dataset.id).where(Dataset.folder_id == item_id).limit(1)):
        raise HTTPException(409, "目录内还有数据集，请先移动或删除数据集")
    await db.delete(item)
    await db.commit()
    return {"id": item_id}


@router.get("")
async def datasets(db: DbSession, user: CurrentUser, folder_id: str = "", q: str = Query(default="", max_length=120)):
    query = select(Dataset).where(*scope(Dataset, user))
    if folder_id:
        query = query.where(Dataset.folder_id == folder_id)
    if q:
        query = query.where(Dataset.name.contains(q, autoescape=True))
    items = (await db.scalars(query.order_by(Dataset.created_at.desc()))).all()
    counts = (await db.execute(select(DatasetFile.dataset_id, func.count(), func.sum(DatasetFile.size)).where(*scope(DatasetFile, user)).group_by(DatasetFile.dataset_id))).all()
    sizes = {key: (count, size) for key, count, size in counts}
    return [{**info(item), "file_count": sizes.get(item.id, (0, 0))[0], "size": sizes.get(item.id, (0, 0))[1]} for item in items]


@router.post("", status_code=201)
async def add_dataset(data: DatasetInput, db: DbSession, user: CurrentUser):
    await owned(DatasetFolder, data.folder_id, db, user, lock=True)
    item = Dataset(id=str(uuid.uuid4()), **identity(user), **data.model_dump())
    db.add(item)
    await commit(db)
    await db.refresh(item)
    return info(item)


@router.get("/{item_id}")
async def dataset(item_id: str, db: DbSession, user: CurrentUser):
    return info(await owned(Dataset, item_id, db, user))


@router.put("/{item_id}")
async def edit_dataset(item_id: str, data: DatasetInput, db: DbSession, user: CurrentUser):
    await owned(DatasetFolder, data.folder_id, db, user, lock=True)
    item = await owned(Dataset, item_id, db, user, lock=True)
    item.name, item.folder_id, item.description = data.name, data.folder_id, data.description
    await commit(db)
    return info(item)


@router.delete("/{item_id}")
async def delete_dataset(item_id: str, db: DbSession, user: CurrentUser):
    item = await owned(Dataset, item_id, db, user, lock=True)
    if await db.scalar(select(DatasetFile.id).where(DatasetFile.dataset_id == item_id).limit(1)):
        raise HTTPException(409, "数据集内还有文件，请先删除文件")
    await db.delete(item)
    await db.commit()
    return {"id": item_id}


@router.get("/{item_id}/files")
async def files(item_id: str, db: DbSession, user: CurrentUser, q: str = Query(default="", max_length=200), modality: Modality | None = None):
    await owned(Dataset, item_id, db, user)
    query = select(DatasetFile).where(DatasetFile.dataset_id == item_id, *scope(DatasetFile, user))
    if q:
        query = query.where(DatasetFile.name.contains(q, autoescape=True))
    if modality:
        query = query.where(DatasetFile.modality == modality)
    return [info(item) for item in await db.scalars(query.order_by(DatasetFile.created_at.desc(), DatasetFile.id))]


@router.post("/{item_id}/files", status_code=201)
async def upload(item_id: str, db: DbSession, user: CurrentUser, modality: Modality = Form(...), file: UploadFile = File(...)):
    await owned(Dataset, item_id, db, user)
    name = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
    if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
        raise HTTPException(400, "文件名无效或超过200字")
    limit = MAX_MEDIA if modality in ("image", "video") else MAX_DATA
    data = await file.read(limit + 1)
    await file.close()
    if len(data) > limit:
        raise HTTPException(413, f"此类型文件不能超过{limit // 1024 // 1024}MB")
    mime, rows = await run_in_threadpool(inspect_file, data, Path(name).suffix.lower(), modality)
    item = DatasetFile(id=str(uuid.uuid4()), **identity(user), name=name, dataset_id=item_id,
                       modality=modality, mime=mime, size=len(data), row_count=rows, sha256=hashlib.sha256(data).hexdigest())
    await owned(Dataset, item_id, db, user, lock=True)
    path = storage(item.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await run_in_threadpool(path.write_bytes, data)
        db.add(item)
        await db.commit()
    except Exception:
        await db.rollback()
        path.unlink(missing_ok=True)
        raise
    await db.refresh(item)
    return info(item)


async def owned_file(dataset_id, file_id, db, user):
    item = await owned(DatasetFile, file_id, db, user)
    if item.dataset_id != dataset_id:
        raise HTTPException(404, "文件不存在")
    return item


@router.get("/{item_id}/files/{file_id}/content")
async def content(item_id: str, file_id: str, db: DbSession, user: CurrentUser):
    item = await owned_file(item_id, file_id, db, user)
    path = storage(item.id)
    if not path.is_file():
        raise HTTPException(410, "文件内容缺失，请重新上传")
    return FileResponse(path, filename=item.name, media_type=item.mime, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/{item_id}/files/{file_id}/preview")
async def preview(item_id: str, file_id: str, db: DbSession, user: CurrentUser, q: str = Query(default="", max_length=200), page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    item = await owned_file(item_id, file_id, db, user)
    if item.modality in ("image", "video"):
        raise HTTPException(400, "图像或视频请读取文件内容")
    path = storage(item.id)
    if not path.is_file():
        raise HTTPException(410, "文件内容缺失，请重新上传")
    data = await run_in_threadpool(path.read_bytes)
    if item.modality == "text":
        lines = data.decode("utf-8-sig").splitlines()
        matches = [line for line in lines if not q or q.casefold() in line.casefold()]
        text = "\n".join(matches)
        return {"text": text[:20000], "total": len(matches), "truncated": len(text) > 20000}
    rows = await run_in_threadpool(read_rows, data, Path(item.name).suffix.lower())
    columns = list(dict.fromkeys(key for row in rows for key in row))
    matches = [row for row in rows if not q or q.casefold() in json.dumps(row, ensure_ascii=False).casefold()]
    start = (page - 1) * page_size
    return {"rows": matches[start:start + page_size], "columns": columns, "total": len(matches)}


@router.delete("/{item_id}/files/{file_id}")
async def delete_file(item_id: str, file_id: str, db: DbSession, user: CurrentUser):
    await owned(Dataset, item_id, db, user, lock=True)
    item = await owned_file(item_id, file_id, db, user)
    await owned(DatasetFile, file_id, db, user, lock=True)
    if await db.scalar(select(DataModel.id).where(DataModel.source_file_id == file_id).limit(1)):
        raise HTTPException(409, "文件已被数据模型引用，请先解除绑定或删除模型")
    await db.execute(delete(DatasetFile).where(DatasetFile.id == item.id, *scope(DatasetFile, user)))
    await db.commit()
    try:
        storage(item.id).unlink(missing_ok=True)
    except OSError:
        logger.warning("Dataset file {} deleted from catalog; disk cleanup pending", item.id)
    return {"id": item.id}
