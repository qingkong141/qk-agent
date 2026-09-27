"""Persistent event-driven analysis and managed platform polling."""
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, FiniteFloat, model_validator
from sqlalchemy import select, update
from app.api.datasets import NameInput, identity, scope
from app.api.pipeline import Node, Edge
from app.api.studio import RevisionInput
from app.dependencies import CurrentUser, DbSession
from app.models.realtime import RealtimeTask
from app.services.realtime_engine import calculate, merge_window, timestamp

router = APIRouter(prefix='/studio/realtime', tags=['realtime'])


class Source(BaseModel):
    kind: Literal['push', 'platform'] = 'push'
    table: str = Field(default='m_infusion', pattern=r'^m[a-zA-Z0-9_]{0,119}$')
    device_id: str = Field(default='', max_length=100, pattern=r'^[a-zA-Z0-9_-]*$')
    metric: str = Field(default='metric.infusion.heartBeat.DevicesPower', max_length=300, pattern=r'^metric\.[^.\s]+\.[^.\s]+\.[^.\s]+$')
    poll_seconds: int = Field(default=10, ge=5, le=300)
    lookback_minutes: int = Field(default=15, ge=1, le=1440)


class Config(BaseModel):
    source: Source = Field(default_factory=Source)
    window_minutes: int = Field(default=15, ge=1, le=1440)
    nodes: list[Node] = Field(min_length=2, max_length=20)
    edges: list[Edge] = Field(max_length=20)

    @model_validator(mode='after')
    def graph(self):
        ids = {node.id for node in self.nodes}
        sources = [node for node in self.nodes if node.kind == 'source']
        outputs = [node for node in self.nodes if node.kind == 'output']
        if len(ids) != len(self.nodes) or len(sources) != 1 or len(outputs) != 1:
            raise ValueError('需要唯一的数据输入和输出，节点不能重复')
        if any(edge.from_id not in ids or edge.to not in ids or edge.to == sources[0].id or edge.from_id == outputs[0].id for edge in self.edges):
            raise ValueError('连线端点无效')
        seen = set(); current = sources[0].id
        while current:
            if current in seen: raise ValueError('不能包含循环')
            seen.add(current)
            following = [edge.to for edge in self.edges if edge.from_id == current]
            if len(following) > 1 or sum(edge.to == current for edge in self.edges) > 1: raise ValueError('每个节点只支持一个前序和后序')
            if not following and current != outputs[0].id: raise ValueError('请连接到结果输出')
            current = following[0] if following else None
        if seen != ids: raise ValueError('有未连接的节点')
        for node in self.nodes:
            if node.kind not in ('source', 'output', 'filter', 'fill', 'aggregate', 'align', 'constant', 'extreme'):
                raise ValueError('实时分析支持过滤、聚合、补齐、频率对齐、恒值和极值检测')
            if node.kind not in ('source', 'output') and node.field != 'value': raise ValueError('当前实时算子处理标准指标字段value')
            if node.kind == 'filter' and node.compare not in ('eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'exists', 'missing'): raise ValueError('请选择数值或空值判断')
            if node.kind in ('filter', 'fill', 'constant') and not (node.kind == 'filter' and node.compare in ('exists', 'missing')):
                try: value = float(node.value)
                except ValueError: raise ValueError('请填写有效数值参数')
                if not math.isfinite(value): raise ValueError('请填写有限数值')
                if node.kind == 'constant' and (not value.is_integer() or value < 2 or value > 5000): raise ValueError('恒值连续条数需为2到5,000的整数')
            if node.kind in ('aggregate', 'align') and (not float(node.interval).is_integer() or node.interval > 1440): raise ValueError('时间间隔需为1到1,440分钟的整数')
        return self


class TaskInput(NameInput):
    config: Config
    expected_revision: int | None = Field(default=None, ge=1)


class Point(BaseModel):
    deviceId: str = Field(min_length=1, max_length=100)
    time: str = Field(max_length=80)
    value: FiniteFloat | None

    @model_validator(mode='before')
    @classmethod
    def strict_value(cls, data):
        if isinstance(data, dict) and data.get('value') is not None and (isinstance(data['value'], bool) or not isinstance(data['value'], (int, float))):
            raise ValueError('value需为数值或null')
        return data

    @model_validator(mode='after')
    def valid_time(self):
        timestamp(self.time)
        if not self.deviceId.strip(): raise ValueError('设备编号不能为空')
        return self


class Batch(BaseModel):
    rows: list[Point] = Field(min_length=1, max_length=1000)


def info(item, full=True):
    runtime = {key: value for key, value in item.runtime.items() if key != 'input'}
    if not full: runtime = {key: value for key, value in runtime.items() if key not in ('rows', 'trace')}
    return {'id': item.id, 'name': item.name, 'revision': item.revision, 'status': item.status,
            'config': item.config, 'runtime': runtime, 'updated_at': item.updated_at}


async def owned(item_id, db, user, lock=False):
    where = (RealtimeTask.id == item_id, *scope(RealtimeTask, user))
    if lock:
        result = await db.execute(update(RealtimeTask).where(*where).values(status=RealtimeTask.status))
        if not result.rowcount: raise HTTPException(404, '实时任务不存在')
    item = await db.scalar(select(RealtimeTask).where(*where).execution_options(populate_existing=True))
    if not item: raise HTTPException(404, '实时任务不存在')
    return item


async def process(item, data, db):
    if item.status != 'running': raise HTTPException(409, '任务未运行，请先启动')
    try:
        inputs, repeated, expired, watermark = merge_window(item.runtime.get('input', []), data, item.config['window_minutes'])
        rows, trace = calculate(item.config, inputs)
        json.dumps(rows, allow_nan=False)
    except (ValueError, OverflowError) as error:
        item.status = 'error'; item.runtime = {**item.runtime, 'error': str(error), 'checked_at': datetime.now(timezone.utc).isoformat()}
        await db.commit()
        raise HTTPException(400, str(error))
    item.runtime = {**item.runtime, 'input': inputs, 'rows': rows, 'trace': trace, 'error': '', 'source_message': '',
        'batch_count': item.runtime.get('batch_count', 0) + 1, 'received': item.runtime.get('received', 0) + len(data),
        'duplicates': item.runtime.get('duplicates', 0) + repeated, 'expired': item.runtime.get('expired', 0) + expired,
        'window_count': len(inputs), 'watermark': watermark, 'processed_at': datetime.now(timezone.utc).isoformat()}
    await db.commit(); await db.refresh(item)
    return info(item)


@router.get('')
async def listing(db: DbSession, user: CurrentUser):
    return [info(item, False) for item in await db.scalars(select(RealtimeTask).where(*scope(RealtimeTask, user)).order_by(RealtimeTask.updated_at.desc()))]


@router.post('', status_code=201)
async def create(data: TaskInput, db: DbSession, user: CurrentUser):
    item = RealtimeTask(id=str(uuid.uuid4()), name=data.name, config=data.config.model_dump(by_alias=True), **identity(user))
    db.add(item); await db.commit(); await db.refresh(item)
    return info(item)


@router.get('/{item_id}')
async def get(item_id: str, db: DbSession, user: CurrentUser):
    return info(await owned(item_id, db, user))


@router.put('/{item_id}')
async def edit(item_id: str, data: TaskInput, db: DbSession, user: CurrentUser):
    item = await owned(item_id, db, user, True)
    if item.status == 'running': raise HTTPException(409, '请先停止任务，再修改编排')
    if item.revision != data.expected_revision: raise HTTPException(409, '配置已更新，请刷新')
    item.name = data.name; item.config = data.config.model_dump(by_alias=True); item.revision += 1; item.runtime = {}; item.status = 'stopped'
    await db.commit(); await db.refresh(item)
    return info(item)


@router.post('/{item_id}/start')
async def start(item_id: str, data: RevisionInput, db: DbSession, user: CurrentUser, request: Request):
    from app.services.realtime_poll import launch, validate_source
    item = await owned(item_id, db, user, True)
    if item.revision != data.expected_revision: raise HTTPException(409, '配置已更新，请刷新')
    if item.status == 'running': return info(item)
    validate_source(item, request.headers.get('X-Platform-Token'))
    item.status = 'running'; item.runtime = {**item.runtime, 'error': '', 'started_at': datetime.now(timezone.utc).isoformat()}
    await db.commit(); await db.refresh(item)
    launch(item, request.headers.get('X-Platform-Token'), user)
    return info(item)


@router.post('/{item_id}/stop')
async def stop(item_id: str, db: DbSession, user: CurrentUser):
    from app.services.realtime_poll import cancel
    item = await owned(item_id, db, user, True)
    item.status = 'stopped'; await db.commit(); await db.refresh(item)
    cancel(item_id)
    return info(item)


@router.post('/{item_id}/ingest')
async def ingest(item_id: str, data: Batch, db: DbSession, user: CurrentUser):
    item = await owned(item_id, db, user, True)
    if item.config['source']['kind'] != 'push': raise HTTPException(409, '此任务从平台读取数据，请勿混入手工上报')
    return await process(item, [row.model_dump() for row in data.rows], db)


@router.delete('/{item_id}')
async def remove(item_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    item = await owned(item_id, db, user, True)
    if item.status == 'running': raise HTTPException(409, '请先停止任务再删除')
    if item.revision != data.expected_revision: raise HTTPException(409, '配置已更新，请刷新')
    await db.delete(item); await db.commit()
    return {'id': item_id}
