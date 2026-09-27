"""Authenticated, read-only MCP services backed by studio and platform data."""
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Literal

import httpx
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.datasets import scope
from app.config import settings
from app.db.session import async_session
from app.dependencies import get_current_user

DEFINITIONS = [
    ('products', '产品物模型', '查询工作台产品和字段规范'),
    ('devices', '设备目录', '按名称查找工作台或平台设备'),
    ('telemetry', '设备时序', '读取设备历史指标'),
    ('conditions', '工况分析', '按真实数据计算趋势及阈值越界'),
    ('knowledge', '操作知识', '查询平台功能、流程和维护知识'),
    ('datasets', '数据集目录', '查询业务目录与多模态文件清单'),
    ('modeling', '数据模型', '查询主题域和分层模型字段'),
    ('realtime', '实时任务', '查询任务运行状态和最近结果'),
    ('data-services', '数据服务', '读取治理后的开放接口结果'),
    ('locations', '设备位置', '查询设备登记位置及对应设备编号'),
    ('maps', '地图服务', '查询平台授权地图与终端坐标，需要PM/PE定位服务在线'),
]
SERVERS = {key: FastMCP(name, stateless_http=True, json_response=True, streamable_http_path='/',
    transport_security=TransportSecuritySettings(allowed_hosts=['localhost:*', '127.0.0.1:*', '[::1]:*'],
        allowed_origins=['http://localhost:*', 'http://127.0.0.1:*', *[v for v in settings.cors_origin_list if v!='*']])) for key,name,_ in DEFINITIONS}


def principal(ctx):
    request = ctx.request_context.request
    return request, request.scope['studio_principal']


@SERVERS['products'].tool()
async def products_catalog(ctx: Context) -> dict:
    """查询当前账号的产品物模型、字段、单位和有效范围。"""
    from app.api.device_assistant import catalog
    _,user=principal(ctx)
    async with async_session() as db: return {'products': (await catalog(db,user))['products']}


@SERVERS['devices'].tool()
async def devices_catalog(ctx: Context, source: Literal['studio','platform']='platform', name: str='') -> dict:
    """按设备名称查询目录；platform为基础物联平台，studio为工作台。平台返回code用于时序查询。"""
    from app.api.device_assistant import catalog, platform_devices
    request,user=principal(ctx)
    if source=='platform': return await platform_devices(request,user,name)
    async with async_session() as db:
        devices=(await catalog(db,user))['devices']
        return {'devices':[v for v in devices if name in v['name'] or name in v['code']]}


async def readings(ctx, source, device_id, metric, table, minutes):
    from app.api.device_assistant import history_rows
    from app.api.applications import read_source
    from app.services.application_schema import Binding
    request,user=principal(ctx)
    if not 1<=minutes<=1440: raise ValueError('查询范围需为1～1440分钟')
    async with async_session() as db:
        if source=='studio': return await history_rows(device_id,metric,minutes,db,user)
        binding=Binding(kind='platform',platform={'device_id':device_id,'metric':metric,'table':table,'lookback_minutes':minutes})
        return (await read_source(binding,db,user,request.headers.get('X-Platform-Token')))['rows']


@SERVERS['telemetry'].tool()
async def telemetry_history(ctx: Context, device_id: str, metric: str, source: Literal['studio','platform']='platform', table: str='m_infusion', minutes: int=60) -> dict:
    """查询指标历史。平台device_id使用目录code；工作台使用目录id。平台metric必须是metric.产品.报文类型.指标完整路径。"""
    rows=await readings(ctx,source,device_id,metric,table,minutes)
    return {'rows':rows,'count':len(rows),'source':source,'metric':metric}


@SERVERS['conditions'].tool()
async def condition_analysis(ctx: Context, device_id: str, metric: str, source: Literal['studio','platform']='platform', table: str='m_infusion', minutes: int=60, lower: float|None=None, upper: float|None=None) -> dict:
    """读取真实设备指标并计算趋势、均值、极值、阈值越界次数和排查建议。没有明确阈值时传null。"""
    import math
    from app.services.device_assistant import analyze
    if any(v is not None and not math.isfinite(v) for v in (lower,upper)) or (lower is not None and upper is not None and lower>upper): raise ValueError('阈值无效')
    return {**analyze(await readings(ctx,source,device_id,metric,table,minutes),lower,upper),'source':source,'metric':metric}


@SERVERS['knowledge'].tool()
async def knowledge_lookup(ctx: Context, query: str='') -> dict:
    """检索平台操作流程、接口规范和设备指标排查知识。资料之外的问题需说明缺少来源。"""
    from app.services.device_assistant import KNOWLEDGE
    principal(ctx)
    words=query.replace('，',' ').split()
    ranked=sorted(KNOWLEDGE,key=lambda d:sum(word in d['title']+d['text'] for word in words),reverse=True)
    return {'documents':ranked[:5]}


@SERVERS['datasets'].tool()
async def dataset_catalog(ctx: Context) -> dict:
    """查询数据集业务目录、数据集及文件类型和行数；不读取文件正文。"""
    from app.models.dataset import Dataset, DatasetFolder, DatasetFile
    _,user=principal(ctx)
    async with async_session() as db:
        output={}
        for model,key,fields in [(DatasetFolder,'folders',['id','name']),(Dataset,'datasets',['id','name','folder_id','description']),(DatasetFile,'files',['id','name','dataset_id','modality','row_count'])]:
            values=list(await db.scalars(select(model).where(*scope(model,user)).limit(101)))
            output[key]=[{field:getattr(v,field) for field in fields} for v in values[:100]];output[key+'_truncated']=len(values)>100
        return output


@SERVERS['modeling'].tool()
async def model_catalog(ctx: Context) -> dict:
    """查询主题域、ODS/DWD/DIM/DWS/ADS分层模型以及字段。"""
    from app.models.data_model import DataModel, ThemeDomain
    _,user=principal(ctx)
    async with async_session() as db:
        domains=list(await db.scalars(select(ThemeDomain).where(*scope(ThemeDomain,user)).limit(100)))
        models=list(await db.scalars(select(DataModel).where(*scope(DataModel,user)).limit(101)))
        return {'domains':[{'id':v.id,'name':v.name} for v in domains],'models':[{'id':v.id,'name':v.name,'table_name':v.table_name,'domain_id':v.domain_id,'layer':v.layer,'fields':v.fields} for v in models[:100]],'truncated':len(models)>100}


@SERVERS['realtime'].tool()
async def realtime_status(ctx: Context, task_id: str='') -> dict:
    """查询实时任务目录或指定任务状态和最近一次结果，保留数据来源时间。"""
    from app.models.realtime import RealtimeTask
    from app.api.realtime import owned,info
    _,user=principal(ctx)
    async with async_session() as db:
        if task_id: return info(await owned(task_id,db,user))
        tasks=list(await db.scalars(select(RealtimeTask).where(*scope(RealtimeTask,user)).limit(101)))
        return {'tasks':[{'id':t.id,'name':t.name,'status':t.status,'processed_at':t.runtime.get('processed_at')} for t in tasks[:100]],'truncated':len(tasks)>100}


@SERVERS['data-services'].tool()
async def data_service_result(ctx: Context, service_id: str='') -> dict:
    """不传ID时列出当前账号数据服务；传入service_id读取最新治理结果（最多1,000条）。"""
    from app.models.studio import StudioArtifact
    from app.api.applications import read_source
    from app.services.application_schema import Binding
    _,user=principal(ctx)
    async with async_session() as db:
        if service_id: return await read_source(Binding(kind='data_service',id=service_id),db,user)
        values=list(await db.scalars(select(StudioArtifact).where(StudioArtifact.kind=='data_service',*scope(StudioArtifact,user)).limit(101)))
        return {'services':[{'id':v.id,'name':v.name,'enabled':v.config['enabled'],'fields':v.config['fields'],'captured_at':v.config['captured_at']} for v in values[:100]],'truncated':len(values)>100}


@SERVERS['locations'].tool()
async def device_location(ctx: Context, name: str='', source: Literal['studio','platform']='platform') -> dict:
    """按名称返回设备登记位置。空位置表示尚未登记，不能编造地图坐标。"""
    result=await devices_catalog(ctx,source,name)
    return {'locations':[{'id':v['id'],'name':v['name'],'code':v['code'],'location':v.get('location') or None} for v in result['devices']], 'total':result.get('total',len(result['devices']))}


@SERVERS['maps'].tool()
async def platform_map(ctx: Context, map_id: int|None=None, terminal_ids: str='') -> dict:
    """查询当前用户获授权的地图目录，指定map_id读取终端坐标；终端编号可逗号分隔。地图服务不可用时如实反馈，不使用登记位置冒充坐标。"""
    from app.services.platform_maps import read_map
    request,user=principal(ctx)
    return await read_map(request.headers,user,map_id,terminal_ids)


class AuthenticatedMCP:
    def __init__(self,app): self.app=app
    async def __call__(self,scope_,receive,send):
        if scope_['type']=='http':
            request=Request(scope_,receive)
            try:
                auth=request.headers.get('Authorization','')
                credentials=HTTPAuthorizationCredentials(scheme='Bearer',credentials=auth[7:]) if auth.startswith('Bearer ') else None
                async with async_session() as db:
                    user=await get_current_user(credentials,db,request.headers.get('X-Platform-Token'),request.headers.get('X-API-Key'),request.headers.get('X-End-User-ID'),request)
                    scope(StudioOwner,user)
                scope_['studio_principal']=user
            except HTTPException as exc:
                await JSONResponse({'detail':exc.detail},status_code=exc.status_code)(scope_,receive,send);return
        await self.app(scope_,receive,send)


# Scope validation also rejects API-key calls with no end-user identity.
from app.models.studio import StudioArtifact as StudioOwner


def mount(app):
    for key,server in SERVERS.items(): app.mount(settings.API_V1_PREFIX+'/studio/mcp/'+key,AuthenticatedMCP(server.streamable_http_app()))


@asynccontextmanager
async def lifespan():
    async with AsyncExitStack() as stack:
        for server in SERVERS.values(): await stack.enter_async_context(server.session_manager.run())
        yield


def catalog():
    return [{'id':key,'name':name,'description':description,'path':settings.API_V1_PREFIX+'/studio/mcp/'+key+'/'} for key,name,description in DEFINITIONS]


@asynccontextmanager
async def connect(service_id,headers):
    if service_id not in SERVERS: raise ValueError('MCP服务不存在')
    from app.main import app
    allowed={key:value for key,value in headers.items() if key.lower() in ('authorization','x-platform-token','x-api-key','x-end-user-id','x-platform-session-id')}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),headers=allowed,timeout=30) as client:
        async with streamable_http_client('http://127.0.0.1:8017'+settings.API_V1_PREFIX+'/studio/mcp/'+service_id+'/',http_client=client) as (reader,writer,_):
            async with ClientSession(reader,writer) as session:
                await session.initialize();yield session
