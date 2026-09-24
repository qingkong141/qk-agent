import json
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, FiniteFloat, model_validator
from sqlalchemy import delete, select, update

from app.dependencies import CurrentUser, DbSession
from app.models.studio import StudioArtifact
from app.api.semantic import SemanticConfig, ThingModel
from app.api.syntax import SyntaxConfig
from app.api.pipeline import PipelineConfig as PipelineV2Config

router = APIRouter(prefix="/studio", tags=["studio"])


class MappingRule(BaseModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    scale: FiniteFloat
    offset: FiniteFloat


class MappingConfig(BaseModel):
    raw: str
    targets: str
    mappings: list[MappingRule] = Field(max_length=500)


class PipelineNode(BaseModel):
    id: str = Field(min_length=1)
    kind: Literal["source", "filter", "dedup", "parse", "condition", "aggregate", "fill", "extreme", "constant", "align", "output"]
    x: FiniteFloat = Field(ge=0)
    y: FiniteFloat = Field(ge=0)
    field: str
    value: str
    interval: FiniteFloat = Field(ge=1)


class PipelineEdge(BaseModel):
    # Keep browser JSON spelling without shadowing Python's reserved keyword.
    from_id: str = Field(alias="from")
    to: str


class PipelineConfig(BaseModel):
    nodes: list[PipelineNode] = Field(max_length=100)
    edges: list[PipelineEdge] = Field(max_length=100)
    input: str
    source: str = "JSON输入"
    chartField: str = "temperature"

    @model_validator(mode="after")
    def validate_links(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or any(edge.from_id not in ids or edge.to not in ids for edge in self.edges):
            raise ValueError("节点编号重复或连线引用不存在的节点")
        return self


class Widget(BaseModel):
    id: str = Field(min_length=1)
    kind: Literal["metric", "line", "bar", "table", "text"]
    title: str
    field: str
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class ApplicationConfig(BaseModel):
    title: str = Field(min_length=1)
    source: str
    defaultField: str = "temperature"
    rows: list[dict] = Field(max_length=10000)
    widgets: list[Widget] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_widgets(self):
        if len({widget.id for widget in self.widgets}) != len(self.widgets):
            raise ValueError("组件编号不能重复")
        return self


class ArtifactInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["mapping", "syntax", "pipeline", "application", "source_model", "target_model"]
    config: dict
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_config(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("配置名称不能为空")
        if len(json.dumps(self.config, ensure_ascii=False).encode()) > 1_000_000:
            raise ValueError("配置不得超过1MB")
        schema = {"mapping": MappingConfig, "syntax": SyntaxConfig, "pipeline": PipelineConfig, "application": ApplicationConfig, "source_model": ThingModel, "target_model": ThingModel}[self.kind]
        if self.kind in ("source_model", "target_model"):
            self.config = {**self.config, "name": self.name}
        if self.kind == "mapping" and self.config.get("schemaVersion") == 2:
            schema = SemanticConfig
        if self.kind == "pipeline" and self.config.get("schemaVersion") == 2:
            schema = PipelineV2Config
        self.config = schema.model_validate(self.config).model_dump(by_alias=True)
        return self


class RevisionInput(BaseModel):
    expected_revision: int = Field(ge=1)


def scope(user: dict):
    # Match the existing API-key end-user isolation, never trust browser-supplied owner IDs.
    if user.get("auth_type") == "api_key" and not user.get("external_user_id"):
        raise HTTPException(400, "API Key调用需提供X-End-User-ID")
    return (
        StudioArtifact.owner_id == user["id"],
        StudioArtifact.external_user_id == user.get("external_user_id", ""),
    )


def serialize(item: StudioArtifact):
    return {"id": item.id, "name": item.name, "kind": item.kind, "config": item.config,
            "revision": item.revision, "published_revision": item.published_revision}


async def get_owned(artifact_id: str, db, user):
    item = await db.scalar(select(StudioArtifact).where(StudioArtifact.id == artifact_id, *scope(user)))
    if not item:
        raise HTTPException(404, "配置不存在")
    return item


async def bind_syntax(data: ArtifactInput, db, user, previous=None):
    if data.kind != 'syntax':
        return
    config = data.config
    # Existing bindings keep their server-owned snapshot even after the original is changed/deleted.
    if previous and previous.config.get('semanticId') == config['semanticId'] and previous.config.get('semanticRevision') == config['semanticRevision']:
        config['semantic'] = previous.config['semantic']
        return
    semantic = await get_owned(config['semanticId'], db, user)
    if semantic.kind != 'mapping' or semantic.config.get('schemaVersion') != 2:
        raise HTTPException(400, '只能绑定语义转换器')
    if semantic.revision != config['semanticRevision']:
        raise HTTPException(409, '语义规则已更新，请刷新并重新选择后测试保存')
    config['semantic'] = {**semantic.config, 'name': semantic.name}


@router.get("/artifacts")
async def list_artifacts(db: DbSession, user: CurrentUser):
    items = await db.scalars(select(StudioArtifact).where(*scope(user), StudioArtifact.kind != 'protocol_run').order_by(StudioArtifact.updated_at.desc()))
    return [serialize(item) for item in items]


@router.post("/artifacts", status_code=201)
async def create_artifact(data: ArtifactInput, db: DbSession, user: CurrentUser):
    scope(user)
    await bind_syntax(data, db, user)
    if data.kind in ("source_model", "target_model") and await db.scalar(select(StudioArtifact.id).where(*scope(user), StudioArtifact.kind == data.kind, StudioArtifact.name == data.name)):
        raise HTTPException(409, "同名物模型已存在，请刷新后选择该模型再修改，或使用其他名称。")
    item = StudioArtifact(id=str(uuid.uuid4()), owner_id=user["id"], external_user_id=user.get("external_user_id", ""),
                          name=data.name, kind=data.kind, config=data.config, revision=1)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return serialize(item)


@router.put("/artifacts/{artifact_id}")
async def update_artifact(artifact_id: str, data: ArtifactInput, db: DbSession, user: CurrentUser):
    item = await get_owned(artifact_id, db, user)
    if item.kind != data.kind:
        raise HTTPException(400, "不能修改配置类型")
    await bind_syntax(data, db, user, item)
    if data.kind in ("source_model", "target_model") and await db.scalar(select(StudioArtifact.id).where(*scope(user), StudioArtifact.kind == data.kind, StudioArtifact.name == data.name, StudioArtifact.id != artifact_id)):
        raise HTTPException(409, "同名物模型已存在，请使用其他名称。")
    result = await db.execute(update(StudioArtifact).where(StudioArtifact.id == artifact_id, *scope(user),
        StudioArtifact.revision == data.expected_revision).values(name=data.name, config=data.config, revision=StudioArtifact.revision + 1))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "配置已被修改，请刷新后重新保存")
    await db.commit()
    await db.refresh(item)
    return serialize(item)


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    item = await get_owned(artifact_id, db, user)
    if item.kind not in ("mapping", "syntax", "pipeline"):
        raise HTTPException(400, "当前仅支持删除转换器及数据管道")
    result = await db.execute(delete(StudioArtifact).where(
        StudioArtifact.id == artifact_id, *scope(user), StudioArtifact.revision == data.expected_revision))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "配置已被修改，请刷新列表后重新删除")
    await db.commit()
    return {"id": artifact_id}


@router.post("/artifacts/{artifact_id}/publish")
async def publish_artifact(artifact_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    item = await get_owned(artifact_id, db, user)
    if item.kind != "application" or not item.config.get("widgets"):
        raise HTTPException(400, "当前只支持发布含组件的应用快照")
    result = await db.execute(update(StudioArtifact).where(StudioArtifact.id == artifact_id, *scope(user),
        StudioArtifact.revision == data.expected_revision).values(published_revision=data.expected_revision, published_config=item.config))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "配置已变化，请刷新后再发布")
    await db.commit()
    return {"id": artifact_id, "published_revision": data.expected_revision}


@router.post("/artifacts/{artifact_id}/unpublish")
async def unpublish_artifact(artifact_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    await get_owned(artifact_id, db, user)
    result = await db.execute(update(StudioArtifact).where(StudioArtifact.id == artifact_id, *scope(user),
        StudioArtifact.revision == data.expected_revision).values(published_revision=None, published_config=None))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "配置已变化，请刷新后再停用")
    await db.commit()
    return {"id": artifact_id, "published_revision": None}


@router.get("/artifacts/{artifact_id}/published")
async def get_published(artifact_id: str, db: DbSession, user: CurrentUser):
    item = await get_owned(artifact_id, db, user)
    if item.published_revision is None:
        raise HTTPException(404, "应用尚未发布或已停用")
    return {"id": item.id, "name": item.name, "revision": item.published_revision, "config": item.published_config}
