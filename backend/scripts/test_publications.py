"""Public links are scoped, revocable, published-only, and never disclose credentials."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
os.environ['SECRET_KEY'] = 'publication-regression-only'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import publications, studio, agent_studio
from app.db.session import Base, get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.models.studio import StudioArtifact
from app.models.publication import PublicationAccess
from app.services import publication_access as access


async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {'id': 'owner', 'auth_type': 'jwt', 'external_user_id': ''}
    async def user():
        if not principal: raise HTTPException(401, 'login required')
        return principal
    async def database():
        async with sessions() as db: yield db
    app = FastAPI()
    for module in (publications, studio, agent_studio): app.include_router(module.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = user
    config = {'schemaVersion': 2, 'title': 'Published', 'source': {'kind': 'snapshot', 'rows': [{'battery': 61}]},
              'widgets': [{'id': 'battery', 'kind': 'metric', 'title': 'Battery', 'field': 'battery'}]}
    agent = {'model': 'published-model', 'services': ['knowledge'], 'prompt': 'published instructions'}
    async with sessions() as db:
        db.add(User(id='owner', email='owner@example.test', hashed_password='unused', is_active=True))
        db.add(StudioArtifact(id='app', name='Application', owner_id='owner', external_user_id='', kind='application',
                              revision=2, published_revision=1, config={**config, 'title': 'Draft'}, published_config=config))
        db.add(StudioArtifact(id='agent', name='Agent', owner_id='owner', external_user_id='', kind='studio_agent',
                              revision=2, published_revision=1, config={**agent, 'model': 'draft-model'}, published_config=agent))
        await db.commit()
    calls = []
    async def available(): return [{'id': 'published-model'}, {'id': 'draft-model'}]
    async def run(config, question, headers, connection, history, state, progress, db, user):
        calls.append((config.model, config.prompt, user['id'], len(history)))
        state.update(answer='live result', status='completed')
        await progress(state)
        return state
    with patch.object(agent_studio, 'available_models', available), patch.object(agent_studio, 'run', run):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            async def enable(key):
                response = await client.put(f'/studio/publications/{key}/access', json={'require_login': False, 'expected_revision': 2}, headers={'Authorization': 'Bearer secret-test-credential'})
                assert response.status_code == 200, response.text
                return response.json()['path'].split('/')[-1]
            token = await enable('app')
            agent_token = await enable('agent')
            async with sessions() as db:
                row = await db.get(PublicationAccess, 'app')
                assert token not in row.credentials and 'secret-test-credential' not in row.credentials
                assert row.token_hash == access.digest(token)
            principal.clear()
            assert (await client.get('/studio/publications/app')).status_code == 401
            assert (await client.get('/publications/app')).status_code == 404
            meta = await client.get('/publications/'+token)
            assert meta.status_code == 200 and meta.json()['config']['title'] == 'Published'
            assert meta.json()['config']['source']['rows'] == []
            assert 'secret-test-credential' not in meta.text
            data = await client.get(f'/publications/{token}/data')
            assert data.status_code == 200 and data.json()['rows'][0]['battery'] == 61
            reply = await client.post(f'/publications/{agent_token}/chat', json={'question': 'query', 'history': [{'question': 'previous', 'answer': 'answer'}]})
            assert reply.status_code == 200, reply.text
            events = [json.loads(line) for line in reply.text.splitlines() if line]
            assert events[-1]['type'] == 'done' and events[-1]['revision'] == 1
            assert calls == [('published-model', 'published instructions', 'owner', 1)]
            assert (await client.post(f'/publications/{agent_token}/chat', json={'question': '   '})).status_code == 422
            assert meta.headers['cache-control'] == 'no-store'
            assert (await client.post(f'/publications/{agent_token}/chat', json={'question': 'query', 'model': 'other'})).status_code == 422
            assert (await client.post(f'/publications/{agent_token}/maps/unauthorized', json={'longitude': 106, 'latitude': 29, 'zoom': 15})).status_code == 404
            principal.update(id='other', auth_type='jwt', external_user_id='')
            assert (await client.get('/studio/publications/app/access')).status_code == 404
            assert (await client.get('/studio/publications/app')).status_code == 404
            principal.update(id='owner', external_user_id='different')
            assert (await client.get('/studio/publications/app')).status_code == 404
            principal['external_user_id'] = ''
            duplicate = await client.post('/studio/artifacts/app/publish', json={'expected_revision': 2, 'require_login': True})
            assert duplicate.status_code == 409
            assert (await client.get('/publications/'+token)).json()['revision'] == 1
            stale = await client.put('/studio/publications/app/access', json={'require_login': True, 'expected_revision': 1})
            assert stale.status_code == 409
            changed = await client.put('/studio/publications/app/access', json={'require_login': True, 'expected_revision': 2})
            assert changed.status_code == 200 and changed.json()['path'] == '/published/app'
            assert (await client.get('/publications/'+token)).status_code == 404
            # Session renewal persists rotated credentials without disclosing them.
            async with sessions() as db:
                await access.configure(db, 'app', False, {'x-platform-token': 'expired', 'x-platform-refresh-token': 'refresh'}, principal)
                await db.commit()
                renewing = access.unseal((await db.get(PublicationAccess, 'app')).credentials)['token']
            async def renew(session):
                assert session.owner_id == 'owner'
                session.refresh_token = 'rotated-refresh'
                return 'renewed-access'
            with patch.object(publications.PlatformSession, 'access_token', renew):
                assert (await client.get(f'/publications/{renewing}/data')).status_code == 200
            async with sessions() as db:
                headers = access.unseal((await db.get(PublicationAccess, 'app')).credentials)['headers']
                assert headers['x-platform-token'] == 'renewed-access' and headers['x-platform-refresh-token'] == 'rotated-refresh'
            newer = await enable('app')
            assert newer != token
            assert (await client.post('/studio/artifacts/app/unpublish', json={'expected_revision': 2})).status_code == 200
            assert (await client.get('/publications/'+newer)).status_code == 404
            published = await client.post('/studio/artifacts/app/publish', json={'expected_revision': 2, 'require_login': False})
            assert published.status_code == 200, published.text
            assert (await client.get('/publications/'+newer)).status_code == 404
            current = (await client.get('/studio/publications/app/access')).json()['path'].split('/')[-1]
            assert (await client.get('/publications/'+current)).json()['config']['title'] == 'Draft'
            assert (await client.post('/studio/agents/agent/stop', json={'expected_revision': 2})).status_code == 200
            assert (await client.get('/publications/'+agent_token)).status_code == 404
            async with sessions() as db:
                owner = await db.get(User, 'owner'); owner.is_active = False; await db.commit()
            assert (await client.get('/publications/'+current)).status_code == 404
    await engine.dispose()
    print('PASS: publication snapshots, anonymous app/stream, ownership, encrypted credentials, revocation and disabled owner')


asyncio.run(main())
