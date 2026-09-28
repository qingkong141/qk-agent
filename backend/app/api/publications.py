"""Standalone runtimes always execute the server's published snapshot."""
import asyncio
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.api import agent_studio, applications, mcp_services, studio
from app.dependencies import CurrentUser, DbSession
from app.models.publication import PublicationAccess
from app.models.studio import StudioArtifact
from app.models.user import User
from app.services import publication_access as access
from app.services.application_schema import ApplicationConfig
from app.services.realtime_auth import PlatformSession
from app.services.amap_preview import MapInput

def no_cache(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'


router = APIRouter(tags=['publications'], dependencies=[Depends(no_cache)])
session_lock = asyncio.Lock()


class AccessInput(BaseModel):
    require_login: bool = True
    expected_revision: int = Field(ge=1)


class ChatInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=2000)
    history: list[agent_studio.HistoryTurn] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def bounded(self):
        agent_studio.Question(question=self.question, history=self.history)
        return self


def published(item):
    if not item or item.kind not in ('application', 'studio_agent') or not item.published_revision:
        raise HTTPException(404, '内容尚未发布、已停用或链接已失效')
    return item


async def settings(item, db):
    link = await db.get(PublicationAccess, item.id)
    token = access.unseal(link.credentials)['token'] if link and link.enabled else None
    return {'require_login': not bool(token), 'path': '/share/'+token if token else '/published/'+item.id}


@router.get('/studio/publications/{item_id}/access')
async def get_access(item_id: str, db: DbSession, user: CurrentUser):
    item = await studio.get_owned(item_id, db, user)
    return await settings(item, db)


@router.put('/studio/publications/{item_id}/access')
async def set_access(item_id: str, data: AccessInput, request: Request, db: DbSession, user: CurrentUser):
    item = published(await studio.get_owned(item_id, db, user))
    if item.revision != data.expected_revision:
        raise HTTPException(409, '配置已更新，请刷新后重试')
    await access.configure(db, item.id, data.require_login, request.headers, user)
    await db.commit()
    return await settings(item, db)


async def shared(token, db, credentials=False):
    if len(token) != 43:
        raise HTTPException(404, '分享链接不存在或已失效')
    link = await db.scalar(select(PublicationAccess).where(PublicationAccess.token_hash == access.digest(token), PublicationAccess.enabled.is_(True)))
    item = published(await db.get(StudioArtifact, link.artifact_id) if link else None)
    owner = await db.get(User, item.owner_id)
    if not owner or not owner.is_active:
        raise HTTPException(404, '分享链接不存在或已失效')
    user = {'id': item.owner_id, 'external_user_id': item.external_user_id, 'auth_type': link.auth_type}
    headers = {}
    if credentials:
        async with session_lock:
            await db.refresh(link)
            if not link.enabled or link.token_hash != access.digest(token):
                raise HTTPException(404, '分享链接已失效')
            payload = access.unseal(link.credentials)
            headers = payload['headers']
            if headers.get('x-platform-token'):
                session = PlatformSession(headers['x-platform-token'], headers.get('x-platform-refresh-token', ''), item.owner_id)
                try:
                    headers['x-platform-token'] = await session.access_token()
                except ValueError:
                    raise HTTPException(503, '发布账号凭据已失效，请发布者重新登录并更新访问设置') from None
                headers['x-platform-refresh-token'] = session.refresh_token
                link.credentials = access.seal(payload)
                await db.commit()
    # Header lookup must remain case-insensitive for existing MCP clients.
    from starlette.datastructures import Headers
    return item, user, Headers(headers)


def application_config(item):
    raw = item.published_config
    if raw.get('schemaVersion') == 2:
        return ApplicationConfig.model_validate(raw)
    fields = list(dict.fromkeys(key for row in raw.get('rows', []) for key in row))
    return ApplicationConfig.model_validate({'schemaVersion': 2, 'title': raw['title'],
        'source': {'kind': 'snapshot', 'rows': raw.get('rows', [])},
        'widgets': [{**w, 'xField': 'time' if 'time' in fields else next(iter(fields), ''),
                     'columns': fields[:30], 'text': w.get('field', '') if w['kind'] == 'text' else '',
                     'span': 2 if w['kind'] == 'table' else 1} for w in raw['widgets']]})


async def describe(item, db, user):
    result = {'id': item.id, 'name': item.name, 'kind': item.kind, 'revision': item.published_revision}
    if item.kind == 'application':
        config = application_config(item).model_dump(by_alias=True)
        # Source identifiers and embedded input rows are not part of the page design.
        config['source'] = {'kind': 'snapshot', 'rows': []}
        config.pop('rows', None)
        result['config'] = config
    else:
        services = await agent_studio.mcp_registry.catalog(db, user)
        result['services'] = [{'id': s['id'], 'name': s['name'], 'path': 'https://mcp.amap.com/mcp' if s['path'].rstrip('/') == 'https://mcp.amap.com/mcp' else ''}
                              for s in services if s['id'] in item.published_config['services']]
    return result


async def read_data(item, db, user, headers):
    if item.kind != 'application': raise HTTPException(400, '当前内容不是应用')
    config = application_config(item)
    result = applications.apply_logic(config, await applications.read_source(config.source, db, user, headers.get('X-Platform-Token')))
    applications.check_widgets(config, result)
    return {'revision': item.published_revision, **result}


async def chat(item, data, db, user, headers):
    if item.kind != 'studio_agent': raise HTTPException(400, '当前内容不是智能体')
    question = agent_studio.Question.model_validate(data.model_dump())
    return await agent_studio.stream_run(agent_studio.Config.model_validate(item.published_config), question, db, user, SimpleNamespace(headers=headers), item.published_revision)


async def map_image(item, service_id, data, db, user):
    if item.kind != 'studio_agent' or service_id not in item.published_config['services']:
        raise HTTPException(404, '该服务不属于已发布智能体')
    return await mcp_services.map_preview(service_id, data, db, user)


@router.get('/studio/publications/{item_id}')
async def private_view(item_id: str, db: DbSession, user: CurrentUser):
    return await describe(published(await studio.get_owned(item_id, db, user)), db, user)


@router.get('/studio/publications/{item_id}/data')
async def private_data(item_id: str, request: Request, db: DbSession, user: CurrentUser):
    return await read_data(published(await studio.get_owned(item_id, db, user)), db, user, request.headers)


@router.post('/studio/publications/{item_id}/chat')
async def private_chat(item_id: str, data: ChatInput, request: Request, db: DbSession, user: CurrentUser):
    return await chat(published(await studio.get_owned(item_id, db, user)), data, db, user, request.headers)


@router.post('/studio/publications/{item_id}/maps/{service_id}')
async def private_map(item_id: str, service_id: str, data: MapInput, db: DbSession, user: CurrentUser):
    return await map_image(published(await studio.get_owned(item_id, db, user)), service_id, data, db, user)


@router.get('/publications/{token}')
async def public_view(token: str, db: DbSession):
    item, user, _ = await shared(token, db)
    return await describe(item, db, user)


@router.get('/publications/{token}/data')
async def public_data(token: str, db: DbSession):
    item, user, headers = await shared(token, db, True)
    return await read_data(item, db, user, headers)


@router.post('/publications/{token}/chat')
async def public_chat(token: str, data: ChatInput, db: DbSession):
    item, user, headers = await shared(token, db, True)
    return await chat(item, data, db, user, headers)


@router.post('/publications/{token}/maps/{service_id}')
async def public_map(token: str, service_id: str, data: MapInput, db: DbSession):
    item, user, _ = await shared(token, db)
    return await map_image(item, service_id, data, db, user)
