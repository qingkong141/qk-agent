"""Account-scoped MCP connections. Platform credentials never leave built-in transports."""
import asyncio
import base64
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.stdio import stdio_client
from sqlalchemy import select

from app.api.datasets import scope
from app.config import settings
from app.models.mcp_service import MCPService
from app.services import studio_mcp, mcp_stdio

PREFIX = 'mcp:'
CATEGORIES = {'nlp':'自然语言处理', 'vision':'计算机视觉', 'multimodal':'多模态生成', 'geo':'地理位置', 'other':'其他服务'}


def cipher():
    if settings.SECRET_KEY == 'change-me-in-production': raise HTTPException(503, '请先配置后台 SECRET_KEY')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('mcp-services:'+settings.SECRET_KEY).encode()).digest()))


def public(item):
    return {'id':PREFIX+item.id, 'name':item.name, 'description':item.description, 'category':item.category,
            'url':item.url, 'path':item.stdio_profile if item.transport=='stdio' else item.url, 'transport':item.transport, 'auth_type':item.auth_type,
            'query_name':item.query_name, 'stdio_profile':item.stdio_profile,
            'header_name':item.header_name, 'has_key':bool(item.credential), 'enabled':item.enabled,
            'timeout_seconds':item.timeout_seconds, 'tools':item.tools, 'checked_at':item.checked_at,
            'revision':item.revision, 'builtin':False}


async def owned(service_id, db, user):
    item = await db.scalar(select(MCPService).where(MCPService.id == service_id.removeprefix(PREFIX), *scope(MCPService,user)))
    if not item: raise HTTPException(404, 'MCP服务不存在')
    return item


async def catalog(db, user):
    builtins = [{**entry, 'url':entry['path'], 'category':'geo' if entry['id'] in ('locations','maps') else 'nlp' if entry['id']=='knowledge' else 'other',
                 'builtin':True, 'enabled':True, 'transport':'streamable_http', 'auth_type':'platform', 'tools':[], 'checked_at':None} for entry in studio_mcp.catalog()]
    return builtins+[public(item) for item in await db.scalars(select(MCPService).where(*scope(MCPService,user)).order_by(MCPService.created_at.desc()))]


async def validate_services(ids, db, user):
    for service_id in ids:
        if service_id in studio_mcp.SERVERS: continue
        item = await owned(service_id,db,user)
        if not item.enabled: raise HTTPException(409, f'MCP服务“{item.name}”已停用')


class QueryCredentialTransport(httpx.AsyncBaseTransport):
    """Insert the key only on the wire; HTTPX request logging keeps the clean URL."""
    def __init__(self, name, key, transport=None):
        self.name=name; self.key=key
        self.transport=transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        outgoing=httpx.Request(request.method, request.url.copy_set_param(self.name,self.key),
                               headers=request.headers, stream=request.stream, extensions=request.extensions)
        return await self.transport.handle_async_request(outgoing)

    async def aclose(self): await self.transport.aclose()


@asynccontextmanager
async def connect(service_id, headers, db, user):
    if service_id in studio_mcp.SERVERS:
        async with studio_mcp.connect(service_id,headers) as session: yield session
        return
    item = await owned(service_id,db,user)
    if not item.enabled: raise HTTPException(409, 'MCP服务已停用')
    outgoing = {}; key = ''
    if item.credential:
        try: key = cipher().decrypt(item.credential.encode()).decode()
        except InvalidToken as exc: raise HTTPException(409, 'MCP密钥无法解密，请重新配置') from exc
        if item.auth_type in ('bearer','api_key'):
            outgoing['Authorization' if item.auth_type=='bearer' else item.header_name] = 'Bearer '+key if item.auth_type=='bearer' else key
    timeout = item.timeout_seconds
    if item.transport=='stdio':
        params=mcp_stdio.parameters(item.stdio_profile,key)
        # External programs can print secrets to stderr; never copy this into API logs.
        with open(os.devnull,'w') as errlog:
            async with stdio_client(params,errlog=errlog) as (reader,writer):
                async with ClientSession(reader,writer,read_timeout_seconds=timedelta(seconds=timeout)) as session:
                    await session.initialize(); yield session
        return
    def transport(): return QueryCredentialTransport(item.query_name,key) if item.auth_type=='query' else None
    if item.transport == 'sse':
        def factory(**kwargs): return httpx.AsyncClient(**kwargs,trust_env=False,follow_redirects=False,transport=transport())
        async with sse_client(item.url,headers=outgoing,timeout=timeout,sse_read_timeout=timeout,httpx_client_factory=factory) as (reader,writer):
            async with ClientSession(reader,writer,read_timeout_seconds=timedelta(seconds=timeout)) as session:
                await session.initialize(); yield session
    else:
        async with httpx.AsyncClient(headers=outgoing,timeout=timeout,trust_env=False,follow_redirects=False,transport=transport()) as client:
            async with streamable_http_client(item.url,http_client=client) as (reader,writer,_):
                async with ClientSession(reader,writer,read_timeout_seconds=timedelta(seconds=timeout)) as session:
                    await session.initialize(); yield session


def check_schema(schema):
    # Do not fetch arbitrary remote references while validating tool arguments.
    def refs(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in ('$ref','$dynamicRef') and (not isinstance(child,str) or not child.startswith('#')):
                    raise HTTPException(400, '工具Schema仅支持文档内引用')
                refs(child)
        elif isinstance(value,list):
            for child in value: refs(child)
    refs(schema)
    try: Draft202012Validator.check_schema(schema)
    except SchemaError as exc: raise HTTPException(400, '工具参数Schema格式无效') from exc


def check_arguments(schema, arguments):
    check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(arguments))
    if errors:
        error = errors[0]
        path = '.'.join(str(p) for p in error.absolute_path) or '参数'
        raise HTTPException(400, f'{path}不符合工具参数要求（{error.validator}）')


async def list_tools(session):
    tools = []; cursor = None; seen = set()
    while True:
        page = await session.list_tools(cursor=cursor)
        for tool in page.tools:
            check_schema(tool.inputSchema)
            if any(v['name']==tool.name for v in tools): raise HTTPException(400, '服务返回了重复的工具名称')
            tools.append(tool.model_dump(by_alias=True,exclude_none=True))
        if len(tools)>200: raise HTTPException(400, '单个服务工具数量超过200，请拆分接入')
        cursor = page.nextCursor
        if not cursor: return tools
        if cursor in seen: raise HTTPException(400, '工具目录分页游标重复')
        seen.add(cursor)


async def discover(service_id, headers, db, user):
    async def execute():
        async with connect(service_id,headers,db,user) as session: return await list_tools(session)
    try: return await asyncio.wait_for(execute(),timeout=120)
    except HTTPException: raise
    except TimeoutError as exc: raise HTTPException(504, 'MCP连接超时，请检查服务地址') from exc
    except Exception as exc:
        if error := http_error(exc): raise error
        raise HTTPException(502, 'MCP连接或工具发现失败，请检查地址、协议和凭据') from exc


def http_error(exc):
    # AnyIO transport task groups wrap exceptions raised inside the MCP context.
    if isinstance(exc,HTTPException): return exc
    if isinstance(exc,BaseExceptionGroup):
        for child in exc.exceptions:
            if found := http_error(child): return found
    return None


def unpack(result):
    if result.structuredContent is not None: return result.structuredContent
    if all(block.type=='text' for block in result.content):
        text='\n'.join(block.text for block in result.content)
        try: return json.loads(text)
        except ValueError: return {'text':text}
    return {'content':[block.model_dump(by_alias=True,exclude_none=True) for block in result.content]}


async def call(service_id, tool_name, arguments, headers, db, user):
    async def execute():
        async with connect(service_id,headers,db,user) as session:
            tool = next((t for t in await list_tools(session) if t['name']==tool_name),None)
            if not tool: raise HTTPException(400, '所选工具已不存在，请重新读取工具目录')
            check_arguments(tool['inputSchema'],arguments)
            result = await session.call_tool(tool_name,arguments)
            data = unpack(result)
            if len(json.dumps(data,ensure_ascii=False).encode())>10*1024*1024: raise HTTPException(400, '工具结果超过10MB，请使用文件链接或缩小请求')
            return {'data':data, 'error':bool(result.isError)}
    try: return await asyncio.wait_for(execute(),timeout=120)
    except HTTPException: raise
    except TimeoutError as exc: raise HTTPException(504, 'MCP工具调用超时') from exc
    except Exception as exc:
        if error := http_error(exc): raise error
        raise HTTPException(502, 'MCP工具调用失败，请检查服务状态和参数') from exc
