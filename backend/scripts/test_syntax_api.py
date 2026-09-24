"""Isolated syntax binding/authoring regression; no production data or real AI calls."""
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
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import studio, syntax
from app.models.studio import StudioArtifact
from app.db.session import get_db
from app.dependencies import get_current_user


async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {'id': 'owner', 'auth_type': 'jwt', 'external_user_id': ''}
    async def db_override():
        async with sessions() as session:
            yield session
    async def user_override(): return principal
    app = FastAPI(); app.include_router(studio.router); app.include_router(syntax.router)
    app.dependency_overrides[get_db] = db_override; app.dependency_overrides[get_current_user] = user_override
    model = {'name':'model','fields':[{'key':'battery','name':'电量','type':'number','unit':'%','required':True,'minimum':0,'maximum':100}]}
    semantic = {'schemaVersion':2,'name':'语义','sourceModel':model,'targetModel':model,'mappings':[],'mode':'script','script':'{"battery":battery}','raw':'{"battery":61}','sampleSource':'sample'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        mapping = (await client.post('/studio/artifacts',json={'name':'语义','kind':'mapping','config':semantic})).json()
        config = {'name':'语法','inputFormat':'json','raw':'{"power":61}','parameters':{},'script':'{"battery":payload.power}','semanticId':mapping['id'],'semanticRevision':1}
        body = {'name':'语法','kind':'syntax','config':config}
        created = await client.post('/studio/artifacts',json=body)
        assert created.status_code == 201, created.text
        item = created.json(); url = '/studio/artifacts/' + item['id']
        assert item['config']['semantic'] == mapping['config']
        # Client-supplied binding contents never replace the server-owned version.
        tampered = {**config, 'semantic':{**semantic,'script':'{"battery":999}'}}
        saved = await client.put(url,json={**body,'config':tampered,'expected_revision':1})
        assert saved.status_code == 200 and saved.json()['config']['semantic']['script'] == semantic['script']
        assert (await client.put(url,json={**body,'expected_revision':1})).status_code == 409
        await client.put('/studio/artifacts/'+mapping['id'],json={'name':'语义v2','kind':'mapping','config':{**semantic,'script':'{"battery":0}'},'expected_revision':1})
        assert (await client.post('/studio/artifacts',json=body)).status_code == 409
        saved = await client.put(url,json={**body,'expected_revision':2})
        assert saved.status_code == 200 and saved.json()['config']['semantic']['script'] == semantic['script']
        generation = {'semanticId':mapping['id'],'semanticRevision':2,'inputFormat':'json','structure':{'power':'number'},'parameterStructure':{},'instruction':'读取电量'}
        fake = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content='```jsonata\n{"battery":payload.power}\n```')))
        with patch('app.api.syntax.create_chat_model',return_value=fake):
            generated = await client.post('/studio/syntax/generate',json=generation)
            assert generated.status_code == 200 and generated.json()['script'] == config['script']
            sent = fake.ainvoke.call_args[0][0][1].content
            assert 'expectedFields' in sent and 'raw' not in sent
            repair = {'script':'{"battery":;}','errorCode':'S0211','position':12}
            assert (await client.post('/studio/syntax/generate',json={**generation,'repair':repair})).status_code == 200
            assert 'S0211' in fake.ainvoke.call_args[0][0][1].content
            assert (await client.post('/studio/syntax/generate',json={**generation,'semanticRevision':1})).status_code == 409
        with patch('app.api.syntax.create_chat_model',side_effect=RuntimeError('private-provider-detail')):
            fail = await client.post('/studio/syntax/generate',json=generation)
            assert fail.status_code == 503 and 'private-provider' not in fail.text
        principal['id'] = 'other'
        assert (await client.post('/studio/artifacts',json={**body,'config':{**config,'semanticRevision':2}})).status_code == 404
        assert (await client.post('/studio/syntax/generate',json=generation)).status_code == 404
        principal['id'] = 'owner'; principal['external_user_id'] = 'different'
        assert (await client.post('/studio/syntax/generate',json=generation)).status_code == 404
        principal['external_user_id'] = ''
        for bad in [{'raw':'[]'},{'inputFormat':'hex-json','raw':'ff'},{'script':' '}]:
            assert (await client.post('/studio/artifacts',json={**body,'config':{**config,**bad}})).status_code == 422
        assert (await client.request('DELETE',url,json={'expected_revision':3})).status_code == 200
        assert any(x['id']==mapping['id'] for x in (await client.get('/studio/artifacts')).json())
        app.dependency_overrides.pop(get_current_user)
        assert (await client.post('/studio/syntax/generate',json=generation)).status_code == 401
    await engine.dispose()
    print('PASS: syntax CRUD, server-owned immutable semantic binding, stale revisions, AI context/failure, owner/end-user isolation, invalid formats, authentication')
asyncio.run(main())
