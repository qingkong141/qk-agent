"""Owned studio products/devices, retained reports and conversational operations."""
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.api.datasets import NameInput, identity, scope
from app.config import settings
from app.dependencies import CurrentUser, DbSession
from app.models.studio import StudioArtifact
from app.services import device_assistant as service
from app.services.realtime_engine import timestamp
from app.services.assistant_stream import answer_stream

router = APIRouter(prefix='/studio/device-assistant', tags=['device-assistant'])


async def owned(item_id, kind, db, user):
    item = await db.scalar(select(StudioArtifact).where(StudioArtifact.id == item_id, StudioArtifact.kind == kind, *scope(StudioArtifact, user)))
    if not item: raise HTTPException(404, '设备、产品或对话不存在')
    return item


def info(item):
    return {'id': item.id, 'name': item.name, 'revision': item.revision, **item.config}


async def catalog(db, user):
    items = list(await db.scalars(select(StudioArtifact).where(StudioArtifact.kind.in_(['device_product', 'device_instance']), *scope(StudioArtifact, user)).order_by(StudioArtifact.name).limit(501)))
    if len(items) > 500: raise HTTPException(400, '当前产品设备目录超过500项，请缩小工作台账号管理范围')
    return {'products': [info(v) for v in items if v.kind == 'device_product'], 'devices': [info(v) for v in items if v.kind == 'device_instance']}


@router.get('/catalog')
async def get_catalog(db: DbSession, user: CurrentUser):
    return await catalog(db, user)


@router.get('/platform-devices')
async def platform_devices(request: Request, user: CurrentUser, name: str = ''):
    token = request.headers.get('X-Platform-Token')
    if not token or not settings.PLATFORM_DEVICE_BASE_URL: raise HTTPException(400, '需要平台账号登录及设备服务地址')
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
            response = await client.get(settings.PLATFORM_DEVICE_BASE_URL.rstrip('/')+'/Device/GetDevicePageList',
                params={'pageIndex': 1, 'pageSize': 50, 'deviceName': name[:120]},
                headers={'Authorization': 'Bearer '+token.removeprefix('Bearer ').strip(), 'UISystemCode': settings.PLATFORM_SYSTEM_CODE})
            response.raise_for_status(); body = response.json()
        if body.get('Status') != 1: raise ValueError('query failed')
        content = body['Content']
        return {'total': content['TotalCount'], 'devices': [{'id': str(v['DeviceID']), 'code': str(v.get('SourceID') or v['DeviceID']),
                 'name': v.get('DeviceName') or str(v['DeviceID']), 'location': v.get('Location') or '', 'type': v.get('DeviceTypeName') or ''} for v in content['List']]}
    except Exception as exc: raise HTTPException(502, '平台设备目录读取失败，请检查登录权限和设备服务') from exc


class Context(BaseModel):
    source: str = Field(default='studio', pattern='^(studio|platform)$')
    device_id: str = Field(default='', max_length=100, pattern=r'^[a-zA-Z0-9_-]*$')
    device_name: str = Field(default='', max_length=120)
    metric: str = Field(default='', max_length=300)
    table: str = Field(default='m_infusion', pattern=r'^m[a-zA-Z0-9_]{0,119}$')


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None
    expected_revision: int | None = None
    context: Context = Field(default_factory=Context)
    model: str | None = Field(default=None, min_length=1, max_length=150)


async def field_for(device_id, metric, db, user):
    device = await owned(device_id, 'device_instance', db, user)
    product = await owned(device.config['product_id'], 'device_product', db, user)
    field = next((f for f in product.config['model']['fields'] if f['key'] == metric), None)
    if not field or field['type'] != 'number': raise HTTPException(400, '请选择设备物模型中的数值指标')
    return device, field


async def history_rows(device_id, metric, minutes, db, user):
    await field_for(device_id, metric, db, user)
    now = datetime.now(timezone.utc); cutoff = (now-timedelta(minutes=minutes)).timestamp()
    # Batches are never silently discarded; reject a query beyond the bounded analysis capacity.
    batches = list(await db.scalars(select(StudioArtifact).where(StudioArtifact.kind == 'device_report',
        StudioArtifact.config['device_id'].as_string() == device_id, *scope(StudioArtifact, user)).order_by(StudioArtifact.updated_at.desc())))
    rows = [{'time': row['time'], 'deviceId': device_id, 'value': row['values'].get(metric)} for batch in batches for row in batch.config['rows'] if cutoff <= timestamp(row['time']) <= now.timestamp()]
    if len(rows) > 1000: raise HTTPException(400, '此范围超过1,000条，请缩短查询时间')
    return rows


@router.post('/ask')
async def ask(data: Ask, db: DbSession, user: CurrentUser, request: Request):
    return await answer(data, db, user, request)


@router.post('/ask-stream')
async def ask_stream(data: Ask, db: DbSession, user: CurrentUser, request: Request):
    return answer_stream(lambda emit: answer(data, db, user, request, emit), db)


async def answer(data, db, user, request, emit=None):
    if not data.question.strip(): raise HTTPException(400, '请输入问题')
    item = await owned(data.thread_id, 'device_chat', db, user) if data.thread_id else None
    if item and item.revision != data.expected_revision: raise HTTPException(409, '对话已更新，请重新打开')
    turns = item.config['turns'] if item else []
    if len(turns) >= 12: raise HTTPException(400, '本次对话已达12轮，请新建对话')
    directory = await catalog(db, user)
    history = [{'question': t['question'], 'answer': t['plan']['explanation'], 'executed': bool(t.get('execution'))} for t in turns]
    if len(json.dumps(directory, ensure_ascii=False)) > 60000: raise HTTPException(400, '产品目录内容过多，请缩小当前账号管理范围')
    if emit: await emit({'type': 'progress', 'message': '正在理解问题，生成处理方案…'})
    args = (data.question.strip(), directory, history, data.context.model_dump())
    options = {}
    if data.model:
        from app.api.agent_studio import selected_chat_model
        options['chat'] = await selected_chat_model(data.model, db, user, streaming=emit is not None)
    plan = await service.generate(*args, emit, **options) if emit else await service.generate(*args, **options)
    result = None
    if plan.action == 'create_device': await owned(plan.product_id, 'device_product', db, user)
    if plan.action in ('analyze', 'report'):
        if emit: await emit({'type': 'progress', 'message': '正在校验设备与指标，读取相关数据…'})
        if data.context.source == 'platform':
            if plan.action == 'report': raise HTTPException(400, '平台报文上报接口尚未接入，请选择工作台设备')
            if not data.context.device_id or plan.device_id != data.context.device_id or plan.metric != data.context.metric:
                raise HTTPException(400, '平台查询必须使用当前选择的设备和指标')
            from app.api.applications import read_source
            from app.services.application_schema import Binding
            try: binding = Binding(kind='platform', platform={'table': data.context.table, 'device_id': plan.device_id, 'metric': plan.metric, 'lookback_minutes': plan.lookback_minutes})
            except ValueError as exc: raise HTTPException(400, '平台指标路径应为 metric.产品.报文类型.指标') from exc
            result = await read_source(binding, db, user, request.headers.get('X-Platform-Token'))
            rows = result['rows']
        else:
            await field_for(plan.device_id, plan.metric, db, user)
            rows = await history_rows(plan.device_id, plan.metric, plan.lookback_minutes, db, user) if plan.action == 'analyze' else []
        if plan.action == 'analyze': result = service.analyze(rows, plan.lower, plan.upper)
    if emit: await emit({'type': 'progress', 'message': '正在整理结果并保存对话…'})
    turn = {'id': str(uuid.uuid4()), 'question': data.question.strip(), 'plan': plan.model_dump(), 'result': result,
            'model': data.model or settings.LLM_MODEL, 'context': data.context.model_dump(), 'time': datetime.now(timezone.utc).isoformat(), 'execution': None}
    config = {'turns': [*turns, turn], 'model': data.model}
    if item:
        changed = await db.execute(update(StudioArtifact).where(StudioArtifact.id == item.id, StudioArtifact.revision == data.expected_revision, *scope(StudioArtifact, user)).values(config=config, revision=StudioArtifact.revision+1))
        if changed.rowcount != 1: raise HTTPException(409, '对话已更新，请重新打开')
    else:
        item = StudioArtifact(id=str(uuid.uuid4()), name=data.question.strip()[:120], kind='device_chat', config=config, **identity(user)); db.add(item)
    await db.commit(); await db.refresh(item)
    return info(item)


class ReportRow(BaseModel):
    time: str = Field(max_length=80)
    values: dict

    @model_validator(mode='after')
    def valid(self):
        timestamp(self.time)
        if timestamp(self.time) > datetime.now(timezone.utc).timestamp()+5: raise ValueError('上报时间不能晚于当前时间')
        if len(json.dumps(self.values, ensure_ascii=False)) > 10000: raise ValueError('单条上报过大')
        return self


class Report(BaseModel):
    rows: list[ReportRow] = Field(min_length=1, max_length=100)


async def save_report(device_id, data, db, user):
    device = await owned(device_id, 'device_instance', db, user)
    product = await owned(device.config['product_id'], 'device_product', db, user)
    fields = {f['key']: f for f in product.config['model']['fields']}
    types = {'number': (int, float), 'string': (str,), 'boolean': (bool,), 'object': (dict,), 'array': (list,)}
    for row in data.rows:
        if any(key not in fields for key in row.values): raise HTTPException(422, '上报包含物模型未定义的字段')
        for key, field in fields.items():
            value = row.values.get(key)
            if value is None:
                if field['required']: raise HTTPException(422, f'请补齐必填指标：{field["name"]}')
                continue
            if not isinstance(value, types[field['type']]) or (field['type']=='number' and (isinstance(value,bool) or not math.isfinite(value))):
                raise HTTPException(422, f'{field["name"]}的类型不符合物模型')
            if field['type']=='number' and ((field['minimum'] is not None and value < field['minimum']) or (field['maximum'] is not None and value > field['maximum'])):
                raise HTTPException(422, f'{field["name"]}超出物模型范围')
    item = StudioArtifact(id=str(uuid.uuid4()), name=device.name+'数据上报', kind='device_report',
        config={'device_id': device_id, 'rows': [r.model_dump() for r in data.rows]}, **identity(user)); db.add(item)
    return {'report_id': item.id, 'count': len(data.rows), 'device_id': device_id}


@router.post('/devices/{device_id}/reports', status_code=201)
async def report(device_id: str, data: Report, db: DbSession, user: CurrentUser):
    result = await save_report(device_id, data, db, user); await db.commit(); return result


class Execute(BaseModel):
    expected_revision: int = Field(ge=1)


@router.post('/threads/{thread_id}/turns/{turn_id}/execute')
async def execute(thread_id: str, turn_id: str, data: Execute, db: DbSession, user: CurrentUser):
    # Claim the revision before any side effects. Repeated execution never duplicates devices/reports.
    changed = await db.execute(update(StudioArtifact).where(StudioArtifact.id == thread_id, StudioArtifact.kind == 'device_chat',
        StudioArtifact.revision == data.expected_revision, *scope(StudioArtifact, user)).values(revision=StudioArtifact.revision+1))
    if changed.rowcount != 1: raise HTTPException(409, '对话已更新或操作已执行，请刷新后查看')
    item = await owned(thread_id, 'device_chat', db, user)
    config = json.loads(json.dumps(item.config)); turn = next((t for t in config['turns'] if t['id'] == turn_id), None)
    if not turn or turn['execution']: raise HTTPException(409, '操作不存在或已执行')
    plan = service.Plan.model_validate(turn['plan'])
    result = None
    if plan.action == 'create_product':
        if await db.scalar(select(StudioArtifact.id).where(StudioArtifact.kind == 'device_product', StudioArtifact.name == plan.product.name, *scope(StudioArtifact, user))): raise HTTPException(409, '同名产品已存在')
        entry = StudioArtifact(id=entry_id('product', plan.product.name, user), name=plan.product.name, kind='device_product', config={'model': plan.product.model_dump()}, **identity(user)); db.add(entry)
        result = {'id': entry.id, 'message': f'产品“{entry.name}”及物模型已创建'}
    elif plan.action == 'create_device':
        await owned(plan.product_id, 'device_product', db, user)
        if await db.scalar(select(StudioArtifact.id).where(StudioArtifact.kind == 'device_instance', StudioArtifact.config['code'].as_string() == plan.device_code, *scope(StudioArtifact, user))): raise HTTPException(409, '设备编号已存在')
        entry = StudioArtifact(id=entry_id('device', plan.device_code, user), name=plan.device_name, kind='device_instance', config={'product_id': plan.product_id, 'code': plan.device_code, 'location': plan.location}, **identity(user)); db.add(entry)
        result = {'id': entry.id, 'message': f'设备“{entry.name}”已创建'}
    elif plan.action == 'report':
        if turn['context']['source'] != 'studio': raise HTTPException(400, '不能向平台设备上报')
        await field_for(plan.device_id, plan.metric, db, user)
        now = datetime.now(timezone.utc)
        rows = [ReportRow(time=(now-timedelta(seconds=(len(plan.values)-1-i)*plan.interval_seconds)).isoformat(), values={plan.metric: value}) for i,value in enumerate(plan.values)]
        result = await save_report(plan.device_id, Report(rows=rows), db, user)
        result['message'] = f'已保存 {len(rows)} 条设备指标，可继续查询趋势'
    else: raise HTTPException(400, '此回答不需要执行写入')
    turn['execution'] = {**result, 'time': datetime.now(timezone.utc).isoformat()}; item.config = config
    try: await db.commit()
    except IntegrityError as exc:
        await db.rollback(); raise HTTPException(409, '产品名称或设备编号已存在，请刷新目录') from exc
    await db.refresh(item); return info(item)


def entry_id(kind, key, user):
    # A stable primary key enforces scoped uniqueness even for concurrent conversations.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([kind, user['id'], user.get('external_user_id', ''), key], ensure_ascii=False)))


@router.get('/threads')
async def threads(db: DbSession, user: CurrentUser):
    return [{'id': i.id, 'name': i.name, 'revision': i.revision} for i in await db.scalars(select(StudioArtifact).where(StudioArtifact.kind == 'device_chat', *scope(StudioArtifact, user)).order_by(StudioArtifact.updated_at.desc()))]


@router.get('/threads/{thread_id}')
async def thread(thread_id: str, db: DbSession, user: CurrentUser):
    return info(await owned(thread_id, 'device_chat', db, user))


@router.get('/knowledge')
async def knowledge(user: CurrentUser):
    return service.KNOWLEDGE


class RenameThread(NameInput):
    expected_revision: int = Field(ge=1)


@router.put('/threads/{thread_id}')
async def rename_thread(thread_id: str, data: RenameThread, db: DbSession, user: CurrentUser):
    item = await owned(thread_id, 'device_chat', db, user)
    changed = await db.execute(update(StudioArtifact).where(StudioArtifact.id == item.id,
        StudioArtifact.kind == 'device_chat', StudioArtifact.revision == data.expected_revision,
        *scope(StudioArtifact, user)).values(name=data.name, revision=StudioArtifact.revision+1))
    if changed.rowcount != 1: raise HTTPException(409, '对话已更新，请重新打开后操作')
    await db.commit(); await db.refresh(item)
    return info(item)


@router.delete('/threads/{thread_id}')
async def delete_thread(thread_id: str, data: Execute, db: DbSession, user: CurrentUser):
    await owned(thread_id, 'device_chat', db, user)
    changed = await db.execute(delete(StudioArtifact).where(StudioArtifact.id == thread_id,
        StudioArtifact.kind == 'device_chat', StudioArtifact.revision == data.expected_revision,
        *scope(StudioArtifact, user)))
    if changed.rowcount != 1: raise HTTPException(409, '对话已更新，请重新打开后操作')
    await db.commit()
    return {'id': thread_id}
