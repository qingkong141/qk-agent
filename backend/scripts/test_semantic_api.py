"""Isolated schema/API tests: no real model invocation or production DB access."""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import semantic, studio
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def db_override():
        async with sessions() as session:
            yield session
    async def user_override(): return {'id':'semantic-test', 'auth_type':'jwt'}
    app = FastAPI(); app.include_router(studio.router); app.include_router(semantic.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    model = {'name':'test', 'fields':[{'key':'battery','name':'电量','type':'number','unit':'%','required':True,'minimum':0,'maximum':100}]}
    data = {'sourceModel':model,'targetModel':model,'mappings':[], 'instruction':'复制字段'}
    config = {**data, 'schemaVersion':2,'name':'测试转换器','mode':'script','script':'{"battery":battery}', 'raw':'{"battery":86.5}', 'sampleSource':'test'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        body = {'name':'测试转换器','kind':'mapping','config':config}
        response = await client.post('/studio/artifacts',json=body)
        assert response.status_code == 201, response.text
        item = response.json()
        assert item['config']['sourceModel'] == model and item['config']['script'] == config['script']
        response = await client.put('/studio/artifacts/'+item['id'],json={**body,'expected_revision':1})
        assert response.json()['revision'] == 2
        assert (await client.put('/studio/artifacts/'+item['id'],json={**body,'expected_revision':1})).status_code == 409
        assert (await client.get('/studio/artifacts')).json()[0]['config']['targetModel'] == model
        fake = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content='```jsonata\n{"battery":battery}\n```')))
        with patch('app.api.semantic.create_chat_model',return_value=fake):
            response = await client.post('/studio/semantic/generate',json=data)
            assert response.json()['script'] == '{"battery":battery}'
            assert 'raw' not in fake.ainvoke.call_args[0][0][1].content
            repair = {'script':'{"battery": 1; "other": 2}', 'errorCode':'S0202','position':14}
            response = await client.post('/studio/semantic/generate',json={**data,'currentScript':'{"battery": $lookup($$, "battery")}', 'repair':repair,'instruction':'要'*2000})
            assert response.status_code == 200, response.text
            messages = fake.ainvoke.call_args[0][0]
            assert 'currentScript' in messages[1].content and 'S0202' in messages[1].content
            assert '不能用分号' in messages[0].content
            assert (await client.post('/studio/semantic/generate',json={**data,'repair':{**repair,'errorCode':'T2001'}})).status_code == 422
        with patch('app.api.semantic.create_chat_model',side_effect=RuntimeError('private provider detail')):
            response = await client.post('/studio/semantic/generate',json=data)
            assert response.status_code == 503 and 'private' not in response.text
        bad = {**model,'fields':model['fields']*2}
        assert (await client.post('/studio/semantic/generate',json={**data,'targetModel':bad})).status_code == 422
        app.dependency_overrides.pop(get_current_user)
        assert (await client.post('/studio/semantic/generate',json=data)).status_code == 401
    await engine.dispose()
    print('PASS: semantic schema roundtrip, revisions, conflict, AI generation, sample exclusion, provider failure, duplicate fields, authentication')
asyncio.run(main())
