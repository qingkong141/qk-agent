"""Real MCP SDK transport, scoped tools and agent publication isolation."""
import asyncio
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI,HTTPException
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import agent_studio as api, agent_models
from app.db.session import Base,get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact
from app.services import studio_mcp as mcp
import app.main as main_module


async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def user():return principal
    async def mcp_user(*args):
        if args[2]!='test': raise HTTPException(401,'missing token')
        return principal.copy()
    async def database():
        async with sessions() as db: yield db
    async def models(): return [{'id':'test-model'}]
    class Model:
        def bind_tools(self,definitions): self.count=0;return self
        async def ainvoke(self,messages):
            self.count+=1
            return AIMessage(content='',tool_calls=[{'name':'products_catalog','args':{},'id':'call-one','type':'tool_call'}]) if self.count==1 else AIMessage(content='已读取产品物模型。')
    app=FastAPI();app.include_router(api.router,prefix='/api/v1');app.include_router(agent_models.router,prefix='/api/v1');mcp.mount(app)
    app.dependency_overrides[get_current_user]=user;app.dependency_overrides[get_db]=database
    async with sessions() as db:
        db.add(StudioArtifact(id='product',owner_id='owner',external_user_id='',name='产品',kind='device_product',config={'model':{'name':'产品','fields':[{'key':'battery','type':'number','name':'电量','unit':'%','required':True,'minimum':0,'maximum':100}]}}))
        db.add(StudioArtifact(id='private-product',owner_id='other',external_user_id='',name='隔离产品',kind='device_product',config={'model':{'name':'隔离产品','fields':[]}}))
        await db.commit()
    with ExitStack() as stack:
        for target,name,value in [(main_module,'app',app),(mcp,'async_session',sessions),(mcp,'get_current_user',mcp_user),(api,'available_models',models),(api,'create_chat_model',lambda *args,**kwargs:Model())]: stack.enter_context(patch.object(target,name,value))
        async with mcp.lifespan(),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8017',headers={'X-Platform-Token':'test'}) as client:
            for entry in mcp.catalog():
                async with mcp.connect(entry['id'],{'X-Platform-Token':'test'}) as session:
                    tools=(await session.list_tools()).tools
                    assert len(tools)==1,entry
                    if entry['id']=='products':
                        result=api.unpack(await session.call_tool('products_catalog',{}));assert [p['id'] for p in result['products']]==['product'],result
                    if entry['id'] in ['knowledge','datasets','modeling','realtime','data-services']:
                        result=await session.call_tool(tools[0].name,{});assert not result.isError,result
            unauthorized=await client.post('/api/v1/studio/mcp/products/',json={'jsonrpc':'2.0','id':1,'method':'tools/list'},headers={'X-Platform-Token':''});assert unauthorized.status_code==401
            hostile=await client.post('/api/v1/studio/mcp/products/',json={'jsonrpc':'2.0','id':1,'method':'tools/list'},headers={'Origin':'https://untrusted.example','Accept':'application/json, text/event-stream'});assert hostile.status_code==403
            base='/api/v1/studio/agents';config={'model':'test-model','prompt':'查询产品','services':['products']}
            r=await client.post(base,json={'name':'设备管理助手','config':config});assert r.status_code==201,r.text
            item=r.json();path=base+'/'+item['id']
            run=await client.post(base+'/debug',json={'config':config,'question':'查询产品'});assert run.status_code==200,run.text
            assert run.json()['trace'][0]['result']['products'][0]['id']=='product'
            assert (await client.post(path+'/publish',json={'expected_revision':1})).status_code==200
            assert (await client.put(path,json={'name':'助手草稿','config':{**config,'prompt':'新草稿'},'expected_revision':1})).status_code==200
            assert (await client.post(path+'/invoke',json={'question':'查询产品'})).json()['revision']==1
            assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==409
            principal['id']='other'
            assert (await client.get(path)).status_code==404
            async with mcp.connect('products',{'X-Platform-Token':'test'}) as session:
                result=api.unpack(await session.call_tool('products_catalog',{}));assert result['products'][0]['id']=='private-product'
            principal['id']='owner'
            assert (await client.post(path+'/stop',json={'expected_revision':2})).status_code==200
            assert (await client.post(path+'/invoke',json={'question':'query'})).status_code==409
            assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==200
            assert (await client.post(base,json={'name':'bad','config':{**config,'model':'invented'}})).status_code==400
            assert (await client.post(base+'/debug',json={'config':{**config,'services':['unknown']},'question':'q'})).status_code==422
            with patch.object(agent_models.settings,'SECRET_KEY','isolated-agent-model-key'),patch.object(agent_models,'chat_model',lambda item:Model()):
                connection=await client.post('/api/v1/studio/agent-models',json={'name':'custom model','base_url':'https://model.example/v1','model':'custom-model','api_key':'isolated-key'})
                assert connection.status_code==201,connection.text
                custom={**config,'model':connection.json()['id']}
                response=await client.post(base+'/debug',json={'config':custom,'question':'查询产品'})
                assert response.status_code==200,response.text
                assert response.json()['trace'][0]['result']['products'][0]['id']=='product'
                response=await client.post(base,json={'name':'custom agent','config':custom});assert response.status_code==201,response.text
                custom_path=base+'/'+response.json()['id']
                assert (await client.post(custom_path+'/publish',json={'expected_revision':1})).status_code==200
                response=await client.post(custom_path+'/invoke',json={'question':'查询产品'})
                assert response.status_code==200 and response.json()['revision']==1,response.text
    await engine.dispose();print('PASS: 10 MCP SDK handshakes, tool execution, auth/origin/owner isolation, model validation, agent debug, publication snapshots, stop/delete')


if __name__=='__main__': asyncio.run(main())
