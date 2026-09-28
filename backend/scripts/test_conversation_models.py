"""Model selection reaches providers, preserves history and respects account ownership."""
import asyncio
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from langchain_core.messages import AIMessage, AIMessageChunk
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import device_assistant, insights, agent_studio, agent_models
from app.db.session import Base, get_db
from app.dependencies import get_current_user
from app.models.agent_model import AgentModel
from app.models.data_model import ThemeDomain, DataModel
from app.models.studio import StudioArtifact


async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def database():
        async with sessions() as db:yield db
    async def user():return principal
    app=FastAPI()
    for module in (device_assistant,insights,agent_studio):app.include_router(module.router)
    app.dependency_overrides[get_db]=database;app.dependency_overrides[get_current_user]=user
    config={'model':'server-default','prompt':'Read devices','services':['products']}
    async with sessions() as db:
        for key,owner in [('one','owner'),('two','owner'),('private','other')]:
            db.add(AgentModel(id=key,name='Name '+key,owner_id=owner,external_user_id='',base_url='https://example.test/v1',model=key,credential='never exposed'))
        db.add(ThemeDomain(id='domain',name='Domain',owner_id='owner',external_user_id=''))
        db.add(DataModel(id='data',name='Data',owner_id='owner',external_user_id='',domain_id='domain',table_name='readings',layer='DWD',fields=[],source_file_id='bound-file'))
        db.add(StudioArtifact(id='agent',name='Agent',owner_id='owner',external_user_id='',kind='studio_agent',config=config,published_config=config,published_revision=1))
        await db.commit()
    calls=[]
    class Model:
        def __init__(self,name):self.name=name
        def content(self,messages):
            context=json.loads(messages[1].content);calls.append((self.name,context))
            return json.dumps({'action':'clarify' if 'SQLite' in messages[0].content else 'answer','explanation':self.name})
        async def ainvoke(self,messages):return AIMessage(content=self.content(messages))
        async def astream(self,messages):
            for char in self.content(messages):yield AIMessageChunk(content=char)
    async def available():return [{'id':'server-default'},{'id':'server-other'}]
    async def agent_run(config,question,headers,connection,history,state,progress,db,user):
        state.update(answer=connection.model if connection else config.model,status='completed')
        return state
    with ExitStack() as stack:
        stack.enter_context(patch.object(agent_models,'chat_model',lambda item,**kw:Model(item.model)))
        stack.enter_context(patch.object(agent_studio,'available_models',available))
        stack.enter_context(patch.object(agent_studio,'create_chat_model',lambda model,**kw:Model(model)))
        stack.enter_context(patch.object(agent_studio,'run',agent_run))
        stack.enter_context(patch.object(device_assistant.service,'create_chat_model',lambda **kw:Model('default')))
        stack.enter_context(patch.object(insights,'create_chat_model',lambda **kw:Model('default')))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            for kind in ('device-assistant','insights'):
                base='/studio/'+kind
                extra={'domain_id':'domain'} if kind=='insights' else {}
                async def ask(model,thread=None,stream=False):
                    body={**extra,'question':'question'}
                    if model is not None:body['model']=model
                    if thread:body.update(thread_id=thread['id'],expected_revision=thread['revision'])
                    response=await client.post(base+('/ask-stream' if stream else '/ask'),json=body)
                    if not stream:return response,response.json()
                    events=[json.loads(line) for line in response.text.splitlines() if line]
                    return response,events[-1]
                response,data=await ask('custom:one');assert response.status_code==200,response.text
                thread=data['thread'] if kind=='insights' else data
                assert thread['model']=='custom:one' and thread['turns'][-1]['plan']['explanation']=='one'
                response,event=await ask('custom:two',thread,True);assert event['type']=='done',event
                data=event['data'];thread=data['thread'] if kind=='insights' else data
                assert thread['model']=='custom:two' and len(thread['turns'])==2
                assert thread['turns'][0]['model']=='custom:one' and thread['turns'][1]['model']=='custom:two'
                assert calls[-1][0]=='two' and calls[-1][1]['history'],calls[-1]
                loaded=(await client.get(base+'/threads/'+thread['id'])).json();assert loaded['model']=='custom:two'
                for invalid in ('custom:private','custom:missing','unknown'):
                    response,_=await ask(invalid,thread);assert response.status_code in (400,404)
                    _,event=await ask(invalid,thread,True);assert event['type']=='error'
                loaded=(await client.get(base+'/threads/'+thread['id'])).json();assert loaded['revision']==thread['revision']
                response,data=await ask(None);assert response.status_code==200,response.text
                value=data['thread'] if kind=='insights' else data
                assert value['turns'][-1]['plan']['explanation']=='default'
                response,data=await ask('server-other');assert response.status_code==200,response.text
                value=data['thread'] if kind=='insights' else data
                assert value['turns'][-1]['plan']['explanation']=='server-other'
            for route in ('/debug','/debug-stream','/agent/invoke','/agent/invoke-stream'):
                body={'question':'question','model':'custom:two'}
                if 'debug' in route:body['config']=config
                response=await client.post('/studio/agents'+route,json=body);assert response.status_code==200,response.text
                answer=json.loads(response.text.splitlines()[-1]) if route.endswith('stream') else response.json()
                assert answer['answer']=='two',answer
                response=await client.post('/studio/agents'+route,json={**body,'model':'custom:private'})
                assert response.status_code==404,response.text
            async with sessions() as db:
                agent=await db.get(StudioArtifact,'agent')
                assert agent.config==config and agent.published_config==config and agent.revision==1
    await engine.dispose()
    print('PASS: both chat providers, stream/nonstream, retained model/history, defaults, scoped selection, draft/published overrides without configuration writes')


if __name__=='__main__':asyncio.run(main())
