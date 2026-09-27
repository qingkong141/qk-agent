"""Isolated API regression tests. Never connect to the configured production DB."""
import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.studio import router
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact
from app.core.exceptions import validation_exception_handler


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {"id": "owner-1", "auth_type": "jwt", "external_user_id": ""}
    async def db_override():
        async with sessions() as session:
            yield session
    async def user_override():
        return principal
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    config = {"title": "Cold room", "source": "Explicit demo dataset", "rows": [{"temperature": 4.2}], "widgets": [
        {"id": "a", "kind": "metric", "title": "Temperature", "field": "temperature", "color": "#167d9a"}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post('/studio/artifacts', json={"name": "Demo", "kind": "application", "config": config})
        assert response.status_code == 201, response.text
        item = response.json(); url = '/studio/artifacts/' + item['id']
        assert (await client.get(url + '/published')).status_code == 404
        assert (await client.post(url + '/publish', json={"expected_revision": 1})).status_code == 200
        changed = {**config, "title": "Edited draft"}
        assert (await client.put(url, json={"name": "Demo", "kind": "application", "config": changed, "expected_revision": 1})).json()['revision'] == 2
        assert (await client.get(url + '/published')).json()['config']['title'] == 'Cold room'
        assert (await client.put(url, json={"name": "Demo", "kind": "application", "config": changed, "expected_revision": 1})).status_code == 409
        assert (await client.post(url + '/publish', json={"expected_revision": 1})).status_code == 409
        principal['id'] = 'other-owner'
        assert (await client.get('/studio/artifacts')).json() == []
        assert (await client.get(url + '/published')).status_code == 404
        principal['id'] = 'owner-1'; principal['external_user_id'] = 'other-end-user'; principal['auth_type'] = 'api_key'
        assert (await client.get('/studio/artifacts')).json() == []
        principal['external_user_id'] = ''
        assert (await client.get('/studio/artifacts')).status_code == 400
        principal['auth_type'] = 'jwt'
        assert (await client.post(url + '/unpublish', json={"expected_revision": 2})).status_code == 200
        assert (await client.get(url + '/published')).status_code == 404
        assert (await client.post('/studio/artifacts', json={"name": "Bad", "kind": "application", "config": {}})).status_code == 422
        assert (await client.post('/studio/artifacts', json={"name": " " , "kind": "application", "config": config})).status_code == 422
        mapping = {"name": "Delete regression", "kind": "mapping", "config": {"raw": "{}", "targets": "", "mappings": []}}
        created = (await client.post('/studio/artifacts', json=mapping)).json()
        delete_url = '/studio/artifacts/' + created['id']
        model = (await client.post('/studio/artifacts', json={"name": "Keep model", "kind": "source_model", "config": {"name": "Keep model", "fields": [{"key": "id", "name": "ID", "type": "string"}]}})).json()
        assert 'id' in model, model
        async def remove(revision=1):
            return await client.request('DELETE', delete_url, json={"expected_revision": revision})
        principal['id'] = 'other-owner'
        assert (await remove()).status_code == 404
        principal['id'] = 'owner-1'; principal['external_user_id'] = 'other-end-user'
        assert (await remove()).status_code == 404
        principal['external_user_id'] = ''; principal['auth_type'] = 'api_key'
        assert (await remove()).status_code == 400
        principal['auth_type'] = 'jwt'
        assert (await client.request('DELETE', delete_url, json={})).status_code == 422
        assert (await client.put(delete_url, json={**mapping, "expected_revision": 1})).status_code == 200
        assert (await remove()).status_code == 409
        assert any(x['id'] == created['id'] for x in (await client.get('/studio/artifacts')).json())
        assert (await remove(2)).status_code == 200
        assert (await remove(2)).status_code == 404
        remaining = {x['id'] for x in (await client.get('/studio/artifacts')).json()}
        assert created['id'] not in remaining and model['id'] in remaining and item['id'] in remaining
        assert (await client.request('DELETE', url, json={"expected_revision": 2})).status_code == 200
        assert item['id'] not in {x['id'] for x in (await client.get('/studio/artifacts')).json()}
    await engine.dispose()
    print('PASS: create, read, update, publish snapshot isolation, stale writes, stale publish, owner isolation, external-user isolation, missing identity, unpublish, malformed config, empty name')
    print('PASS: delete converter, stale delete, owner/end-user isolation, missing identity/revision, repeat delete, model preservation and stopped application deletion')


asyncio.run(main())
