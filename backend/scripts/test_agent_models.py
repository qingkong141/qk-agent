"""Isolated persistence and OpenAI wire-format tests; never changes live model connections."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import agent_models as api, agent_studio as agents
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.agent_model import AgentModel
from app.models.studio import StudioArtifact


async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(AgentModel.__table__.create)
        await conn.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = dict(id='owner', auth_type='jwt', external_user_id='')
    async def user(): return principal
    async def database():
        async with sessions() as db: yield db
    async def unavailable(): raise HTTPException(503, 'default catalog offline')
    app = FastAPI(); app.include_router(api.router); app.include_router(agents.router)
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = database
    base = '/studio/agent-models'
    data = dict(name='设备模型', base_url='https://model.example/v1/', model='device-model', api_key='test-secret-not-real')
    with patch.object(api.settings, 'SECRET_KEY', 'isolated-test-encryption-key'), patch.object(agents, 'available_models', unavailable):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for url in ['ftp://host', 'https://host/v1/chat/completions', 'https://host/v1?token=secret', 'https://user:pass@host']:
                assert (await client.post(base, json={**data, 'base_url':url})).status_code == 422
            assert (await client.post(base, json={**data, 'api_key':''})).status_code == 400
            response = await client.post(base, json=data)
            assert response.status_code == 201, response.text
            item = response.json(); path = base+'/'+item['id']
            assert item['base_url'] == 'https://model.example/v1' and item['has_key'] and 'api_key' not in item
            assert data['api_key'] not in (await client.get(base)).text
            async with sessions() as db:
                stored = await db.get(AgentModel, item['id'].removeprefix(api.PREFIX))
                encrypted = stored.credential
                assert encrypted != data['api_key'] and api.cipher().decrypt(encrypted.encode()).decode() == data['api_key']
                # Exercise the actual SDK request, not just a stub model factory.
                async def wire(request):
                    assert str(request.url) == 'https://model.example/v1/chat/completions'
                    assert request.headers['authorization'] == 'Bearer '+data['api_key']
                    payload = json.loads(request.content)
                    assert payload['model'] == 'device-model' and payload['tools'][0]['function']['name'] == 'connection_check'
                    assert 'extra_body' not in payload and 'chat_template_kwargs' not in payload
                    return httpx.Response(200, json={'id':'response', 'object':'chat.completion', 'created':1, 'model':'device-model', 'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'call','type':'function','function':{'name':'connection_check','arguments':'{}'}}]}}]})
                async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
                    factory = api.ChatOpenAI
                    with patch.object(api, 'ChatOpenAI', lambda **kw: factory(**kw, http_async_client=http)):
                        result = await client.post(path+'/test')
                        assert result.status_code == 200, result.text
            calls = []
            async def kimi_wire(request):
                payload = json.loads(request.content); calls.append(payload)
                assert payload['max_tokens'] == api.settings.MAX_TOKENS and 'max_completion_tokens' not in payload
                assert payload['chat_template_kwargs'] == {'thinking': False}
                if len(calls) == 1:
                    return httpx.Response(429, headers={'retry-after':'1'}, json={'error':{'message':'rate limit'}})
                message = {'role':'assistant','content':'Connection works'} if payload['messages'][-1]['role'] == 'tool' else {'role':'assistant','content':None,'tool_calls':[{'id':'kimi-call','type':'function','function':{'name':'connection_check','arguments':'{}'}}]}
                return httpx.Response(200, json={'id':'response','object':'chat.completion','created':1,'model':'kimi-k2.6','choices':[{'index':0,'finish_reason':'stop' if message['content'] else 'tool_calls','message':message}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(kimi_wire)) as http:
                factory = api.ChatOpenAI
                with patch.object(api, 'ChatOpenAI', lambda **kw: factory(**kw, http_async_client=http)):
                    connection = SimpleNamespace(model='kimi-k2.6', base_url='https://api.modelarts-maas.com/openai/v1', credential=encrypted)
                    model = api.chat_model(connection).bind_tools([{'type':'function','function':{'name':'connection_check','parameters':{'type':'object','properties':{}}}}])
                    messages = [HumanMessage(content='Check connection')]
                    first = await model.ainvoke(messages)
                    final = await model.ainvoke(messages+[first, ToolMessage(content='{"ok":true}', tool_call_id=first.tool_calls[0]['id'])])
                    assert final.content == 'Connection works' and len(calls) == 3
            other_provider = api.chat_model(SimpleNamespace(model='kimi-k2.6', base_url='https://model.example/v1', credential=encrypted))
            assert other_provider.max_retries == 0 and not other_provider.extra_body
            result = await client.put(path, json={**data, 'name':'已修改', 'api_key':'', 'expected_revision':1})
            assert result.status_code == 200 and result.json()['revision'] == 2
            async with sessions() as db:
                assert (await db.get(AgentModel, stored.id)).credential == encrypted
            assert (await client.put(path, json={**data, 'expected_revision':1})).status_code == 409
            catalog = (await client.get('/studio/agents/catalog')).json()
            assert catalog['warning'] and catalog['models'][0]['id'] == item['id']
            config = dict(model=item['id'], prompt='查设备', services=['products'])
            agent = (await client.post('/studio/agents', json={'name':'助手', 'config':config})).json()
            assert agent['config']['model'] == item['id']
            assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code == 409
            # Existing published references also prevent deleting a connection after editing the draft.
            async with sessions() as db:
                record=await db.get(StudioArtifact,agent['id']);record.published_config=config;record.published_revision=1
                record.config={**config,'model':'other'};await db.commit()
            assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code == 409
            for owner in [dict(id='other',auth_type='jwt',external_user_id=''),dict(id='owner',auth_type='api_key',external_user_id='other')]:
                principal.update(owner)
                assert (await client.get(base)).json() == []
                assert (await client.put(path,json={**data,'expected_revision':2})).status_code == 404
                assert (await client.post(path+'/test')).status_code == 404
                assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code == 404
                assert (await client.post('/studio/agents/debug',json={'config':config,'question':'读取设备'})).status_code == 404
            principal.update(id='owner',auth_type='jwt',external_user_id='')
            class BrokenModel:
                def bind_tools(self,*args,**kwargs): return self
                async def ainvoke(self,*args): raise ValueError(data['api_key'])
            with patch.object(api,'chat_model',lambda item:BrokenModel()):
                result=await client.post(path+'/test')
                assert result.status_code==502 and data['api_key'] not in result.text
            class NoTools(BrokenModel):
                async def ainvoke(self,*args): return AIMessage(content='hello')
            with patch.object(api,'chat_model',lambda item:NoTools()):
                assert (await client.post(path+'/test')).status_code==400
            async with sessions() as db:
                record=await db.get(StudioArtifact,agent['id']);await db.delete(record);await db.commit()
            assert (await client.request('DELETE',path,json={'expected_revision':1})).status_code==409
            assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==200
            assert (await client.get(base)).json()==[]
    await engine.dispose()
    print('PASS: encrypted credentials, masked responses/errors, key retention, URL validation, ownership, revision checks, catalog fallback, reference protection and actual OpenAI tool-call payload')


if __name__ == '__main__': asyncio.run(main())
