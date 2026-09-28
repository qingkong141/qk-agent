"""Incremental model text, validated device proposals and real cancellation."""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from fastapi import HTTPException
from langchain_core.messages import AIMessageChunk
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import device_assistant as api
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact
from app.services.assistant_stream import model_response, answer_stream


async def main():
    samples=['中文含"引号"、反斜杠\\和\n换行', '😀位置']
    for sample in samples:
        raw='```json\n'+json.dumps({'explanation':sample,'sql':'SECRET_SQL'},ensure_ascii=True)+'\n```'
        async def chunks(messages):
            for char in raw: yield AIMessageChunk(content=char)
        events=[]
        async def emit(event):events.append(event)
        result=await model_response(SimpleNamespace(astream=chunks),[],emit,structured=True)
        assert result.content==raw and events[-1]['text']==sample
        assert len(events)>2 and all('SECRET_SQL' not in e['text'] for e in events)
    for invalid in [AIMessageChunk(content=''),AIMessageChunk(content='',tool_call_chunks=[{'name':'lookup','args':'not-json','id':'x','index':0}])]:
        async def invalid_chunks(messages):yield invalid
        try:
            await model_response(SimpleNamespace(astream=invalid_chunks),[],emit)
            raise AssertionError('Empty/incomplete model output must not complete')
        except HTTPException as exc:assert exc.status_code==502

    # A disconnect must close the provider generator and roll back incomplete work.
    waiting=asyncio.Event();cancelled=asyncio.Event();db=SimpleNamespace(rollback=AsyncMock())
    async def slow(messages):
        try:
            yield AIMessageChunk(content='部分回答')
            waiting.set();await asyncio.Event().wait()
        finally:cancelled.set()
    async def operation(emit):
        await model_response(SimpleNamespace(astream=slow),[],emit)
        raise AssertionError('cancelled work must not finish')
    response=answer_stream(operation,db)
    sent=[]
    async def send(message):sent.append(message)
    async def receive():
        await waiting.wait();return {'type':'http.disconnect'}
    await asyncio.wait_for(response({'type':'http','asgi':{'spec_version':'2.0'}},receive,send),2)
    assert cancelled.is_set();db.rollback.assert_awaited_once()

    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:await connection.run_sync(StudioArtifact.__table__.create)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async def database():
        async with sessions() as session:yield session
    async def user():return {'id':'owner','auth_type':'jwt','external_user_id':''}
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[get_db]=database;app.dependency_overrides[get_current_user]=user
    @app.middleware('http')
    async def passthrough(request,call_next):return await call_next(request)
    # Exercise a real SQLAlchemy session under the same middleware cancellation scope as production.
    waiting.clear();cancelled.clear();errors=[]
    class Capture(logging.Handler):
        def emit(self,record):errors.append(record.getMessage())
    handler=Capture(logging.ERROR);logger=logging.getLogger('sqlalchemy.pool');logger.addHandler(handler)
    received=False
    async def disconnect():
        nonlocal received
        if not received:
            received=True
            return {'type':'http.request','body':json.dumps({'question':'cancel'}).encode(),'more_body':False}
        await waiting.wait();return {'type':'http.disconnect'}
    try:
        with patch.object(api.service,'create_chat_model',lambda **kwargs:SimpleNamespace(astream=slow)):
            await asyncio.wait_for(app({'type':'http','asgi':{'version':'3.0','spec_version':'2.0'},'http_version':'1.1',
                'method':'POST','scheme':'http','path':'/studio/device-assistant/ask-stream','query_string':b'',
                'headers':[(b'content-type',b'application/json')],'server':('test',80),'client':('test',1),'root_path':''},disconnect,send),3)
        assert cancelled.is_set() and not errors,errors
        async with sessions() as session:
            from sqlalchemy import select
            assert not list(await session.scalars(select(StudioArtifact)))
    finally:logger.removeHandler(handler)
    plan={'action':'create_product','explanation':'准备创建输液监测产品，请核对字段后确认。','product':{'name':'流式输液监测','fields':[{'key':'battery','name':'电量','type':'number','unit':'%','required':True,'minimum':0,'maximum':100}]}}
    async def proposal(messages):
        raw=json.dumps(plan,ensure_ascii=False)
        for start in range(0,len(raw),4):yield AIMessageChunk(content=raw[start:start+4])
    base='/studio/device-assistant'
    with patch.object(api.service,'create_chat_model',lambda **kwargs:SimpleNamespace(astream=proposal)):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post(base+'/ask-stream',json={'question':'创建产品'})
            events=[json.loads(line) for line in response.text.splitlines() if line]
            assert len([e for e in events if e['type']=='text'])>2
            assert events[-1]['type']=='done',events
            thread=events[-1]['data'];turn=thread['turns'][0]
            assert turn['execution'] is None
            assert not (await client.get(base+'/catalog')).json()['products']
            assert (await client.get(base+'/threads/'+thread['id'])).json()==thread
            response=await client.post(f"{base}/threads/{thread['id']}/turns/{turn['id']}/execute",json={'expected_revision':1})
            assert response.status_code==200,response.text
            assert len((await client.get(base+'/catalog')).json()['products'])==1
            plan['product']=None
            response=await client.post(base+'/ask-stream',json={'question':'bad','thread_id':thread['id'],'expected_revision':2})
            events=[json.loads(line) for line in response.text.splitlines() if line]
            assert events[-1]['type']=='error' and not any(e['type']=='done' for e in events)
            assert (await client.get(base+'/threads/'+thread['id'])).json()['revision']==2
    await engine.dispose()
    print('PASS: incremental Unicode/escaped JSON, no raw SQL leakage, provider cancellation and rollback, streamed device proposals/history, explicit write confirmation, malformed plan rejection')


if __name__=='__main__':asyncio.run(main())
