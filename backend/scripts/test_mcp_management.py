"""Isolated database and real HTTP/SSE MCP server; never modifies platform data."""
import asyncio
import copy
import json
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import mcp_services as services,mcp_flows as flows,agent_studio
from app.db.session import Base,get_db
from app.dependencies import get_current_user
from app.models.mcp_service import MCPService
from app.services import mcp_registry as registry,mcp_flow as runtime


async def main():
    logging.disable(logging.CRITICAL)
    seen=[];invocations=[]
    mcp=FastMCP('test-service',stateless_http=True,json_response=True,streamable_http_path='/')
    @mcp.tool()
    async def analyze(text:str)->dict:
        invocations.append(('analyze',text));return {'text':text.upper(),'length':len(text),'nullable':None}
    @mcp.tool()
    async def collect(value:str)->dict:
        invocations.append(('collect',value));return {'received':value}
    @mcp.tool()
    async def fail()->dict: raise ValueError('expected-test-failure')
    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run(): yield
    external=FastAPI(lifespan=lifespan)
    @external.middleware('http')
    async def record(request,call_next):
        seen.append(dict(request.headers));return await call_next(request)
    external.mount('/mcp',mcp.streamable_http_app())
    external.mount('/legacy',mcp.sse_app())
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(external,log_level='critical',timeout_graceful_shutdown=2))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def user(): return principal.copy()
    async def database():
        async with sessions() as db: yield db
    api=FastAPI();api.include_router(services.router);api.include_router(flows.router)
    api.dependency_overrides[get_current_user]=user;api.dependency_overrides[get_db]=database
    try:
        async with asyncio.timeout(10):
            while not server.started: await asyncio.sleep(.01)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://test',headers={'X-Platform-Token':'platform-private','X-Platform-Session-ID':'session-private'}) as client:
            data={'name':'外部工具','url':f'http://127.0.0.1:{port}/mcp/','category':'nlp','auth_type':'bearer','api_key':'isolated-test-key'}
            for url in ['file:///etc/passwd','https://user:pass@example.com/mcp','https://host/mcp?key=secret']:
                assert (await client.post('/studio/mcp-services',json={**data,'url':url})).status_code==422
            with patch.object(registry.settings,'SECRET_KEY','isolated-encryption'):
                response=await client.post('/studio/mcp-services',json=data);assert response.status_code==201,response.text
                item=response.json();path='/studio/mcp-services/'+item['id']
                assert 'isolated-test-key' not in response.text and 'credential' not in item
                async with sessions() as db:
                    stored=await db.get(MCPService,item['id'].removeprefix('mcp:'));encrypted=stored.credential
                    assert encrypted!='isolated-test-key' and registry.cipher().decrypt(encrypted.encode())==b'isolated-test-key'
                discovery=await client.post(path+'/test');assert discovery.status_code==200,discovery.text
                assert {t['name'] for t in discovery.json()['tools']}=={'analyze','collect','fail'}
                assert seen and all(h.get('authorization')=='Bearer isolated-test-key' for h in seen)
                assert all('x-platform-token' not in h and 'x-platform-session-id' not in h for h in seen)
                invalid=await client.post(path+'/call',json={'tool':'analyze','arguments':{}});assert invalid.status_code==400 and not invocations
                valid=await client.post(path+'/call',json={'tool':'analyze','arguments':{'text':'hello'}});assert valid.json()['data']['text']=='HELLO'
                edited=await client.put(path,json={**data,'api_key':'','expected_revision':1});assert edited.status_code==200
                async with sessions() as db: assert (await db.get(MCPService,stored.id)).credential==encrypted
                assert (await client.put(path,json={**data,'api_key':'','url':'https://different.example/mcp','expected_revision':2})).status_code==400
                sse=(await client.post('/studio/mcp-services',json={**data,'name':'SSE服务','url':f'http://127.0.0.1:{port}/legacy/sse','transport':'sse'})).json()
                print('Checking SSE transport...',flush=True)
                sse_test=await client.post('/studio/mcp-services/'+sse['id']+'/test');assert sse_test.status_code==200,sse_test.text
                sse_call=await client.post('/studio/mcp-services/'+sse['id']+'/call',json={'tool':'collect','arguments':{'value':'sse-result'}})
                assert sse_call.json()['data']['received']=='sse-result'
                class Model:
                    def bind_tools(self,definitions):
                        names=[d['function']['name'] for d in definitions]
                        assert len(names)==len(set(names))==6
                        self.tools=[name for name in names if name.endswith('_analyze')];self.called=False;return self
                    async def ainvoke(self,messages):
                        if self.called: return AIMessage(content='done')
                        self.called=True
                        return AIMessage(content='',tool_calls=[{'name':name,'args':{'text':'agent'},'id':f'call-{i}','type':'tool_call'} for i,name in enumerate(self.tools)])
                async with sessions() as db:
                    config=agent_studio.Config(model='test-model',services=[item['id'],sse['id']],prompt='query')
                    with patch.object(agent_studio,'create_chat_model',lambda *a,**kw:Model()):
                        result=await agent_studio.run(config,'query',{},None,[],{'trace':[],'charts':[]},None,db,principal)
                    assert result['status']=='completed' and len(result['trace'])==2
                    assert all(t['tool']=='analyze' and t['result']['text']=='AGENT' for t in result['trace'])
                print('Checking workflow execution...',flush=True)
                config={'nodes':[{'id':'n_first','name':'文本分析','service_id':item['id'],'tool':'analyze','arguments':{'text':{'$from':'input','path':'/text'}}},
                                 {'id':'n_second','name':'结果接收','service_id':item['id'],'tool':'collect','arguments':{'value':{'$from':'n_first','path':'/text'}}}],
                        'edges':[{'from':'n_first','to':'n_second'}], 'input_schema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}
                for mutate in [lambda c:c['edges'].append({'from':'n_second','to':'n_first'}),lambda c:c.update(edges=[]),lambda c:c['nodes'][0].update(arguments={'text':{'$from':'n_second','path':'/received'}})]:
                    invalid_config=copy.deepcopy(config);mutate(invalid_config)
                    response=await client.post('/studio/mcp-flows',json={'name':'invalid','config':invalid_config});assert response.status_code==422,response.text
                response=await client.post('/studio/mcp-flows',json={'name':'处理流程','config':config});assert response.status_code==201,response.text
                flow=response.json();flow_path='/studio/mcp-flows/'+flow['id']
                assert (await client.post(flow_path+'/invoke',json={'input':{'text':'abc'}})).status_code==409
                assert (await client.post(flow_path+'/publish',json={'expected_revision':1})).status_code==200
                invocations.clear()
                result=(await client.post(flow_path+'/invoke',json={'input':{'text':'abc'}})).json()
                assert result['status']=='completed' and result['revision']==1 and result['outputs']['n_second']['received']=='ABC',result
                assert invocations==[('analyze','abc'),('collect','ABC')]
                invocations.clear()
                assert (await client.post(flow_path+'/invoke',json={'input':{}})).status_code==400 and not invocations
                draft=copy.deepcopy(config);draft['nodes'][0]['arguments']={'text':'draft'}
                assert (await client.put(flow_path,json={'name':'修改草稿','config':draft,'expected_revision':1})).status_code==200
                published=(await client.post(flow_path+'/invoke',json={'input':{'text':'original'}})).json()
                assert published['outputs']['n_second']['received']=='ORIGINAL' and published['revision']==1
                missing=copy.deepcopy(config);missing['nodes'][1]['arguments']['value']['path']='/missing'
                invocations.clear();failure=(await client.post('/studio/mcp-flows/debug',json={'config':missing,'input':{'text':'x'}})).json()
                assert failure['status']=='error' and invocations==[('analyze','x')] and len(failure['trace'])==2
                error_config=copy.deepcopy(config);error_config['nodes'][0].update(tool='fail',arguments={})
                failure=(await client.post('/studio/mcp-flows/debug',json={'config':error_config,'input':{'text':'x'}})).json()
                assert failure['status']=='error' and len(failure['trace'])==1
                assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==409
                principal['id']='other'
                assert (await client.post(path+'/test')).status_code==404
                assert (await client.get(flow_path)).status_code==404
                assert (await client.post(flow_path+'/invoke',json={'input':{'text':'x'}})).status_code==404
                assert (await client.post('/studio/mcp-flows',json={'name':'foreign','config':config})).status_code==404
                principal['id']='owner'
                assert (await client.post(flow_path+'/stop',json={'expected_revision':2})).status_code==200
                assert (await client.post(flow_path+'/invoke',json={'input':{'text':'x'}})).status_code==409
                assert (await client.request('DELETE',flow_path,json={'expected_revision':2})).status_code==200
                assert (await client.request('DELETE',path,json={'expected_revision':1})).status_code==409
                assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==200
        media=CallToolResult(content=[ImageContent(type='image',data='test-base64',mimeType='image/png')])
        assert registry.unpack(media)['content'][0]['type']=='image'
        assert runtime.resolve({'$from':'input','path':'/a~1b/0'}, {'a/b':[None]}, {}) is None
        print('PASS: real Streamable HTTP and SSE discovery/calls, encrypted keys, credential isolation, schemas, typed references, DAG validation, failure stops, ownership, revisions, published snapshots, API invocation and multimodal content')
    finally:
        server.should_exit=True;await task;await engine.dispose()


if __name__=='__main__': asyncio.run(main())
