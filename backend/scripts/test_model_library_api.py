"""Model library persistence and isolation checks, using only an in-memory DB."""
import asyncio
import os
import sys
from pathlib import Path

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api.studio import router
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact


async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = {'id': 'model-owner', 'auth_type': 'jwt'}

    async def db_override():
        async with sessions() as session:
            yield session

    async def user_override():
        return user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    model = {'name': 'ignored', 'fields': [{'key': 'battery', 'name': '电量', 'type': 'number', 'unit': '%', 'required': True, 'minimum': 0, 'maximum': 100}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for kind in ['source_model', 'target_model']:
            body = {'name': ' 通用字段 ', 'kind': kind, 'config': model}
            response = await client.post('/studio/artifacts', json=body)
            assert response.status_code == 201, response.text
            saved = response.json()
            assert saved['config']['name'] == saved['name'] == '通用字段'
            assert saved['config']['fields'] == model['fields']
            assert (await client.post('/studio/artifacts', json=body)).status_code == 409
            url = '/studio/artifacts/' + saved['id']
            changed = {**body, 'config': {**model, 'fields': [{**model['fields'][0], 'maximum': 90}]}, 'expected_revision': 1}
            assert (await client.put(url, json=changed)).json()['revision'] == 2
            assert (await client.put(url, json=changed)).status_code == 409
            # Another account cannot read or overwrite this model, but can use the same name.
            user['id'] = 'other-owner'
            assert all(item['id'] != saved['id'] for item in (await client.get('/studio/artifacts')).json())
            assert (await client.put(url, json=changed)).status_code == 404
            assert (await client.post('/studio/artifacts', json=body)).status_code == 201
            user['id'] = 'model-owner'
            bad = {**body, 'name': 'invalid', 'config': {**model, 'fields': model['fields'] * 2}}
            assert (await client.post('/studio/artifacts', json=bad)).status_code == 422
        items = (await client.get('/studio/artifacts')).json()
        assert len(items) == 2
        assert {item['kind'] for item in items} == {'source_model', 'target_model'}
        assert all(item['config']['fields'][0]['maximum'] == 90 for item in items)
        app.dependency_overrides.pop(get_current_user)
        assert (await client.get('/studio/artifacts')).status_code == 401
    await engine.dispose()
    print('PASS: source/target model persistence, field metadata, separate namespaces, canonical names, duplicates, revisions, stale writes, account isolation, invalid fields and authentication')


asyncio.run(main())
