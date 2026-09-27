"""MCP directory, encrypted connection settings, discovery and explicit tool tests."""
import re
import uuid
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator, ConfigDict
from sqlalchemy import delete, select, update

from app.api.datasets import NameInput, identity, scope
from app.api.studio import RevisionInput
from app.dependencies import CurrentUser, DbSession
from app.models.mcp_service import MCPService
from app.models.studio import StudioArtifact
from app.services import mcp_registry as registry, studio_mcp
from app.services import mcp_stdio
from app.services.mcp_templates import TEMPLATES

router = APIRouter(prefix='/studio/mcp-services', tags=['mcp-services'])


class Input(NameInput):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(default='',max_length=1000)
    category: Literal['nlp','vision','multimodal','geo','other'] = 'other'
    url: str = Field(default='',max_length=1000)
    transport: Literal['streamable_http','sse','stdio'] = 'streamable_http'
    auth_type: Literal['none','bearer','api_key','query','env'] = 'none'
    header_name: str = Field(default='X-API-Key',max_length=100)
    query_name: str = Field(default='key',pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,99}$')
    stdio_profile: str = Field(default='',max_length=100)
    api_key: SecretStr | None = None
    enabled: bool = True
    timeout_seconds: int = Field(default=30,ge=5,le=120)
    expected_revision: int | None = None

    @field_validator('url')
    @classmethod
    def valid_url(cls,value):
        value=value.strip(); url=urlsplit(value)
        if not value: return ''
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.fragment or url.query or any(c.isspace() for c in value):
            raise ValueError('请填写HTTP/HTTPS服务地址，不在地址中填写密钥或查询参数')
        return value

    @model_validator(mode='after')
    def valid_connection(self):
        if self.transport=='stdio':
            if self.url or not self.stdio_profile or self.auth_type not in ('none','env'):
                raise ValueError('stdio请选择后台登记的服务，认证方式为环境变量密钥或无需认证')
        elif not self.url or self.stdio_profile or self.auth_type=='env':
            raise ValueError('HTTP/SSE请填写服务地址，并选择对应认证方式')
        return self

    @field_validator('header_name')
    @classmethod
    def valid_header(cls,value):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,99}',value) or value.lower() in ('host','cookie','content-length','content-type','accept','connection','mcp-session-id'):
            raise ValueError('请填写有效的认证请求头名称')
        return value


class Call(BaseModel):
    tool: str = Field(min_length=1,max_length=200)
    arguments: dict = Field(default_factory=dict)


def values(data, previous=None):
    if data.transport=='stdio':
        p=mcp_stdio.profile(data.stdio_profile)
        if bool(p.secret_env)!=(data.auth_type=='env'):
            raise HTTPException(400,'认证方式与本地服务配置不一致')
    result=data.model_dump(exclude={'api_key','expected_revision'})
    key=data.api_key.get_secret_value().strip() if data.api_key else ''
    if len(key)>4000 or any(ord(c)<32 for c in key): raise HTTPException(400,'密钥格式无效')
    if data.auth_type=='none': result['credential']=''
    elif key: result['credential']=registry.cipher().encrypt(key.encode()).decode()
    elif previous and previous.credential and all(getattr(previous,k)==getattr(data,k) for k in ('auth_type','url','header_name','query_name','stdio_profile','transport')):
        result['credential']=previous.credential
    else: raise HTTPException(400,'请填写认证密钥；更换地址或认证方式后需重新填写')
    return result


@router.get('')
async def listing(db:DbSession,user:CurrentUser): return await registry.catalog(db,user)


@router.get('/templates')
async def templates(user:CurrentUser): return TEMPLATES


@router.get('/stdio-profiles')
async def stdio_profiles(user:CurrentUser): return mcp_stdio.catalog()


@router.post('',status_code=201)
async def create(data:Input,db:DbSession,user:CurrentUser):
    scope(MCPService,user)
    item=MCPService(id=str(uuid.uuid4()),**values(data),**identity(user))
    db.add(item);await db.commit();await db.refresh(item);return registry.public(item)


@router.put('/{service_id}')
async def edit(service_id:str,data:Input,db:DbSession,user:CurrentUser):
    item=await registry.owned(service_id,db,user)
    changed=await db.execute(update(MCPService).where(MCPService.id==item.id,MCPService.revision==data.expected_revision,*scope(MCPService,user))
        .values(**values(data,item),tools=[],checked_at=None,revision=MCPService.revision+1))
    if changed.rowcount!=1: raise HTTPException(409,'配置已更新，请刷新重试')
    await db.commit();await db.refresh(item);return registry.public(item)


@router.delete('/{service_id}')
async def remove(service_id:str,data:RevisionInput,db:DbSession,user:CurrentUser):
    item=await registry.owned(service_id,db,user)
    for artifact in await db.scalars(select(StudioArtifact).where(StudioArtifact.kind.in_(['studio_agent','mcp_flow']),*scope(StudioArtifact,user))):
        for config in (artifact.config,artifact.published_config):
            if config and (service_id in config.get('services',[]) or any(n.get('service_id')==service_id for n in config.get('nodes',[]))):
                raise HTTPException(409,f'服务已被“{artifact.name}”引用，请先移除引用')
    changed=await db.execute(delete(MCPService).where(MCPService.id==item.id,MCPService.revision==data.expected_revision,*scope(MCPService,user)))
    if changed.rowcount!=1: raise HTTPException(409,'配置已更新，请刷新重试')
    await db.commit();return {'id':service_id}


@router.post('/{service_id}/test')
async def test(service_id:str,request:Request,db:DbSession,user:CurrentUser):
    item=None if service_id in studio_mcp.SERVERS else await registry.owned(service_id,db,user)
    revision=item.revision if item else None
    tools=await registry.discover(service_id,request.headers,db,user)
    checked_at=datetime.now(timezone.utc).isoformat()
    if item:
        changed=await db.execute(update(MCPService).where(MCPService.id==item.id,MCPService.revision==revision,*scope(MCPService,user)).values(tools=tools,checked_at=checked_at))
        if changed.rowcount!=1: raise HTTPException(409,'测试期间配置已变化，请重新测试')
        await db.commit()
    return {'id':service_id,'tools':tools,'checked_at':checked_at}


@router.post('/{service_id}/call')
async def call(service_id:str,data:Call,request:Request,db:DbSession,user:CurrentUser):
    return await registry.call(service_id,data.tool,data.arguments,request.headers,db,user)
