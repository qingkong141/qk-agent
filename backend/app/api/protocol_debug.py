"""Version-pinned protocol test sessions. Execution reports are produced by the browser worker."""
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select, update

from app.dependencies import CurrentUser, DbSession
from app.models.studio import StudioArtifact
from app.api.studio import get_owned, scope, serialize, RevisionInput

router = APIRouter(prefix='/studio/debug', tags=['protocol-debug'])
STAGES = ['content', 'format', 'code', 'syntax', 'source', 'semantic']


class DebugInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    configurationId: str | None = None
    syntaxId: str = Field(min_length=1, max_length=36)
    syntaxRevision: int = Field(ge=1)
    semanticId: str = Field(min_length=1, max_length=36)
    semanticRevision: int = Field(ge=1)
    inputFormat: Literal['json', 'hex-json']
    raw: str = Field(max_length=400000)
    parametersText: str = Field(max_length=10000)
    productName: str = Field(min_length=1, max_length=120)
    productKey: str = Field(min_length=1, max_length=120)
    deviceId: str = Field(min_length=1, max_length=120)
    scenario: str = Field(max_length=40)
    power: float = Field(default=61, ge=0, le=100, allow_inf_nan=False)
    deviceStatus: int = 1


class Stage(BaseModel):
    key: Literal['content', 'format', 'code', 'syntax', 'source', 'semantic']
    name: str = Field(max_length=80)
    status: Literal['passed', 'warning', 'failed', 'skipped']
    message: str = Field(max_length=2000)


class RunReport(BaseModel):
    stages: list[Stage] = Field(min_length=6, max_length=6)
    parsed: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    checks: list[dict] = Field(default_factory=list, max_length=200)
    durationMs: int = Field(ge=0, le=3600000)

    @model_validator(mode='after')
    def validate_report(self):
        if [s.key for s in self.stages] != STAGES:
            raise ValueError('校验步骤不完整或顺序错误')
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 600000:
            raise ValueError('运行结果过大')
        return self


class SaveInput(BaseModel):
    runId: str
    expected_revision: int | None = Field(default=None, ge=1)


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_output(fields, output):
    # Recheck the result contract on save; browser reports are execution evidence, not trusted attestations.
    types = {'string': str, 'number': (int, float), 'boolean': bool, 'object': dict, 'array': list}
    for field in fields:
        value = output.get(field['key'])
        if value is None:
            continue
        valid = isinstance(value, types[field['type']])
        if field['type'] == 'number':
            valid = valid and not isinstance(value, bool) and math.isfinite(value)
            valid = valid and (field.get('minimum') is None or value >= field['minimum']) and (field.get('maximum') is None or value <= field['maximum'])
        if not valid:
            raise HTTPException(400, f"{field['name']} 类型或范围校验未通过")


async def typed_owned(item_id, kind, db, user):
    item = await get_owned(item_id, db, user)
    if item.kind != kind:
        raise HTTPException(400, '记录类型不匹配')
    return item


@router.post('/runs', status_code=201)
async def start_run(data: DebugInput, db: DbSession, user: CurrentUser):
    config = data.model_dump()
    previous = None
    if data.configurationId:
        previous = (await typed_owned(data.configurationId, 'protocol_debug', db, user)).config
    # A saved configuration may continue using its historical, server-owned snapshots.
    if previous and (previous['syntaxId'], previous['syntaxRevision']) == (data.syntaxId, data.syntaxRevision):
        syntax = previous['syntax']
    else:
        item = await typed_owned(data.syntaxId, 'syntax', db, user)
        if item.revision != data.syntaxRevision:
            raise HTTPException(409, '语法转换器版本已变化，请刷新并重新选择')
        syntax = {**item.config, 'name': item.name}
    if previous and (previous['semanticId'], previous['semanticRevision']) == (data.semanticId, data.semanticRevision):
        semantic = previous['semantic']
    elif (syntax['semanticId'], syntax['semanticRevision']) == (data.semanticId, data.semanticRevision):
        semantic = syntax['semantic']
    else:
        item = await typed_owned(data.semanticId, 'mapping', db, user)
        if item.revision != data.semanticRevision:
            raise HTTPException(409, '语义转换器版本已变化，请刷新并重新选择')
        if item.config.get('schemaVersion') != 2:
            raise HTTPException(400, '请选择新版语义转换器')
        semantic = {**item.config, 'name': item.name}
    config.update(syntax=syntax, semantic=semantic)
    item = StudioArtifact(id=str(uuid.uuid4()), owner_id=user['id'], external_user_id=user.get('external_user_id', ''),
        name=data.name, kind='protocol_run', revision=1,
        config={'settings': config, 'startedAt': now(), 'status': 'running', 'engine': 'browser-jsonata-worker'})
    db.add(item); await db.commit(); await db.refresh(item)
    return serialize(item)


@router.put('/runs/{run_id}')
async def finish_run(run_id: str, data: RunReport, db: DbSession, user: CurrentUser):
    item = await typed_owned(run_id, 'protocol_run', db, user)
    passed = all(s.status in ('passed', 'warning') for s in data.stages)
    if passed:
        fields = item.config['settings']['semantic']['targetModel']['fields']
        if not fields or set(data.output) != {f['key'] for f in fields} or {c.get('key') for c in data.checks} != set(data.output) or len(data.checks) != len(fields) or any(c.get('ok') is not True for c in data.checks):
            raise HTTPException(400, '通过记录必须包含全部标准字段及校验结果')
        validate_output(fields, data.output)
    config = {**item.config, 'finishedAt': now(), 'status': 'passed' if passed else 'failed', 'report': data.model_dump()}
    result = await db.execute(update(StudioArtifact).where(StudioArtifact.id == run_id, *scope(user), StudioArtifact.revision == 1).values(config=config, revision=2))
    if result.rowcount != 1:
        await db.rollback(); raise HTTPException(409, '运行记录已结束，不能重复写入')
    await db.commit(); await db.refresh(item)
    return serialize(item)


@router.get('/runs')
async def list_runs(db: DbSession, user: CurrentUser):
    items = await db.scalars(select(StudioArtifact).where(*scope(user), StudioArtifact.kind == 'protocol_run').order_by(StudioArtifact.updated_at.desc()).limit(50))
    return [{'id': i.id, 'name': i.name, 'status': i.config['status'], 'startedAt': i.config['startedAt'],
             'deviceId': i.config['settings']['deviceId'], 'syntaxName': i.config['settings']['syntax']['name']} for i in items]


@router.get('/runs/{run_id}')
async def read_run(run_id: str, db: DbSession, user: CurrentUser):
    return serialize(await typed_owned(run_id, 'protocol_run', db, user))


async def passed_settings(run_id, db, user):
    run = await typed_owned(run_id, 'protocol_run', db, user)
    if run.config['status'] != 'passed':
        raise HTTPException(400, '当前运行未通过校验，不能保存配置')
    return run.config['settings']


@router.post('/configs', status_code=201)
async def create_config(data: SaveInput, db: DbSession, user: CurrentUser):
    config = await passed_settings(data.runId, db, user)
    item = StudioArtifact(id=str(uuid.uuid4()), owner_id=user['id'], external_user_id=user.get('external_user_id', ''),
        name=config['name'], kind='protocol_debug', config={**config, 'lastRunId': data.runId}, revision=1)
    db.add(item); await db.commit(); await db.refresh(item)
    return serialize(item)


@router.put('/configs/{config_id}')
async def update_config(config_id: str, data: SaveInput, db: DbSession, user: CurrentUser):
    item = await typed_owned(config_id, 'protocol_debug', db, user)
    config = await passed_settings(data.runId, db, user)
    if config.get('configurationId') != config_id:
        raise HTTPException(400, '运行记录不属于当前配置，请重新运行')
    result = await db.execute(update(StudioArtifact).where(StudioArtifact.id == config_id, *scope(user), StudioArtifact.revision == data.expected_revision).values(
        name=config['name'], config={**config, 'lastRunId': data.runId}, revision=StudioArtifact.revision + 1))
    if result.rowcount != 1:
        await db.rollback(); raise HTTPException(409, '配置已被修改，请刷新后重新保存')
    await db.commit(); await db.refresh(item)
    return serialize(item)


@router.delete('/configs/{config_id}')
async def remove_config(config_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    await typed_owned(config_id, 'protocol_debug', db, user)
    result = await db.execute(delete(StudioArtifact).where(StudioArtifact.id == config_id, *scope(user), StudioArtifact.revision == data.expected_revision))
    if result.rowcount != 1:
        await db.rollback(); raise HTTPException(409, '配置已被修改，请刷新后重新删除')
    await db.commit()
    return {'id': config_id}
