"""SSO/API isolation regression checks; uses only in-memory SQLite and a mock SSO."""
import asyncio
import io
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
os.environ['PLATFORM_SSO_BASE_URL'] = 'https://sso.test/api'
os.environ['SECRET_KEY'] = 'isolated-test-local-key'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.studio import router
from app.config import settings
from app.db.session import get_db
from app.dependencies import CurrentUser
from app.models.studio import StudioArtifact
from app.models.user import User
from app import platform_auth  # Register the focused HTTPX log redaction filter.


SSO_KEY = 'isolated-test-sso-key'


def platform_token(user='1', realm='hospital', **changes):
    claims = {'userID': user, 'iss': realm, 'OrgTid': 'session-1', 'exp': int(time.time()) + 600, 'nbf': 0}
    claims.update(changes)
    return jwt.encode(claims, SSO_KEY, algorithm='HS256')


async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
        await connection.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db_override():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = db_override

    @app.get('/whoami')
    async def whoami(user: CurrentUser):
        return user

    legacy_id = str(uuid.uuid4())
    async with sessions() as db:
        db.add(User(id=legacy_id, email='legacy@test.invalid', hashed_password='unused', api_key='legacy-test-key'))
        await db.commit()
    legacy_jwt = jwt.encode({'sub': legacy_id, 'exp': int(time.time()) + 600}, settings.SECRET_KEY, algorithm='HS256')
    valid = platform_token()
    headers = {'X-Platform-Token': valid}
    config = {'name': 'SSO private draft', 'kind': 'mapping', 'config': {'raw': '{}', 'targets': '', 'mappings': []}}
    remote_mode = 'verify'
    remote_requests = 0

    def sso(request):
        nonlocal remote_requests
        remote_requests += 1
        assert request.method == 'POST'
        assert str(request.url).startswith('https://sso.test/api/SSO/ValidateToken?')
        assert request.headers['UISystemCode'] == settings.PLATFORM_SYSTEM_CODE
        if remote_mode == 'timeout':
            raise httpx.ReadTimeout('mock unavailable', request=request)
        if remote_mode == 'connection':
            raise httpx.ConnectError('mock unavailable', request=request)
        if remote_mode == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://other.test/'})
        if remote_mode == 'server-error':
            return httpx.Response(500, json={'Status': 1})
        if remote_mode == 'bad-json':
            return httpx.Response(200, text='not JSON')
        if remote_mode == 'bool-status':
            return httpx.Response(200, json={'Status': True})
        if remote_mode == 'string-status':
            return httpx.Response(200, json={'Status': '1'})
        if remote_mode == 'missing-status':
            return httpx.Response(200, json={'Content': None})
        if remote_mode == 'array-body':
            return httpx.Response(200, json=[{'Status': 1}])
        if remote_mode == 'reject':
            return httpx.Response(200, json={'Status': 0, 'Content': None})
        if remote_mode == 'accept':
            return httpx.Response(200, json={'Status': 1, 'Content': None})
        token = request.url.params['token']
        try:
            # SSO still checks the signature. Deliberately leave NumericDate
            # checking to the local guard to exercise both trust boundaries.
            jwt.decode(token, SSO_KEY, algorithms=['HS256'], options={'verify_exp': False, 'verify_nbf': False})
        except JWTError:
            return httpx.Response(200, json={'Status': 0, 'Content': None})
        return httpx.Response(200, json={'Status': 1, 'Content': None})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(sso)
    log_output = io.StringIO()
    log_handler = logging.StreamHandler(log_output)
    http_log = logging.getLogger('httpx')
    old_level = http_log.level
    http_log.setLevel(logging.INFO)
    http_log.addHandler(log_handler)
    checks = 0

    async with real_client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        with patch('app.platform_auth.httpx.AsyncClient', side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            assert (await client.get('/whoami')).status_code == 401
            assert remote_requests == 0
            checks += 1

            response = await client.get('/whoami', headers={**headers, 'X-End-User-ID': 'spoofed', 'X-User-ID': legacy_id})
            assert response.status_code == 200, response.text
            principal = response.json()
            owner_id = principal['id']
            assert principal['auth_type'] == 'platform' and principal['external_user_id'] == ''
            assert owner_id != legacy_id
            async with sessions() as db:
                user = await db.get(User, owner_id)
                assert user.api_key is None and user.hashed_password.startswith('$2')
                assert user.email.endswith('@platform.invalid')
            checks += 1

            response = await client.get('/whoami', headers={'X-Platform-Token': 'Bearer ' + platform_token(OrgTid='session-2', exp=int(time.time()) + 900)})
            assert response.json()['id'] == owner_id
            assert remote_requests == 2  # Every access checks SSO; no indefinite acceptance cache.
            checks += 1

            artifact = await client.post('/studio/artifacts', headers=headers, json=config)
            assert artifact.status_code == 201, artifact.text
            artifact_url = '/studio/artifacts/' + artifact.json()['id']
            assert len((await client.get('/studio/artifacts', headers=headers)).json()) == 1
            assert len((await client.get('/studio/artifacts', headers={'X-Platform-Token': platform_token(OrgTid='session-3')})).json()) == 1
            for other in (platform_token('2'), platform_token(realm='other-hospital')):
                other_headers = {'X-Platform-Token': other}
                other_id = (await client.get('/whoami', headers=other_headers)).json()['id']
                assert other_id != owner_id
                assert (await client.get('/studio/artifacts', headers=other_headers)).json() == []
                assert (await client.put(artifact_url, headers=other_headers, json={**config, 'expected_revision': 1})).status_code == 404
                assert (await client.get(artifact_url + '/published', headers=other_headers)).status_code == 404
            checks += 1

            first = (await client.get('/whoami', headers={'X-Platform-Token': platform_token('c', 'a/b')})).json()['id']
            second = (await client.get('/whoami', headers={'X-Platform-Token': platform_token('b/c', 'a')})).json()['id']
            assert first != second
            checks += 1

            async with sessions() as db:
                user = await db.get(User, owner_id)
                user.is_active = False
                await db.commit()
            assert (await client.get('/whoami', headers=headers)).status_code == 403
            assert (await client.get('/studio/artifacts', headers=headers)).status_code == 403
            checks += 1

            before_legacy = remote_requests
            assert (await client.get('/whoami', headers={'Authorization': 'Bearer ' + legacy_jwt})).json()['id'] == legacy_id
            api_principal = (await client.get('/whoami', headers={'X-API-Key': 'legacy-test-key', 'X-End-User-ID': ' end-user '})).json()
            assert api_principal['id'] == legacy_id and api_principal['external_user_id'] == 'end-user'
            assert remote_requests == before_legacy
            assert (await client.get('/whoami', headers={'X-API-Key': 'invalid-key'})).status_code == 401
            assert (await client.get('/whoami', headers={'Authorization': 'Bearer invalid'})).status_code == 401
            checks += 1

            forged = jwt.encode({'userID': '1', 'OrgTid': 'hospital', 'exp': int(time.time()) + 600}, 'wrong-sso-key', algorithm='HS256')
            for bad in (forged, 'not-a-token', ' '):
                # A rejected platform credential may not fall back to a valid
                # unrelated AI key/JWT and silently switch account scope.
                assert (await client.get('/whoami', headers={'X-Platform-Token': bad, 'Authorization': 'Bearer ' + legacy_jwt, 'X-API-Key': 'legacy-test-key'})).status_code == 401
            checks += 1

            remote_mode = 'accept'
            malformed_claims = [
                {'exp': int(time.time()) - 1}, {'nbf': int(time.time()) + 600},
                {'exp': None}, {'exp': 'nan'}, {'exp': 'inf'}, {'nbf': 'nan'},
                {'userID': ''}, {'userID': True}, {'userID': {}}, {'iss': {}}, {'iss': ''},
            ]
            for claims in malformed_claims:
                response = await client.get('/whoami', headers={'X-Platform-Token': platform_token(**claims)})
                assert response.status_code == 401, (claims, response.status_code)
            assert (await client.get('/whoami', headers={'X-Platform-Token': 'not-a-token'})).status_code == 401
            checks += len(malformed_claims) + 1

            for mode, expected in [('timeout', 503), ('connection', 503), ('redirect', 503), ('server-error', 503), ('bad-json', 503), ('bool-status', 401), ('string-status', 401), ('missing-status', 401), ('array-body', 401), ('reject', 401)]:
                remote_mode = mode
                response = await client.get('/whoami', headers={'X-Platform-Token': platform_token('new-user')})
                assert response.status_code == expected, (mode, response.status_code)
                assert valid not in response.text
            checks += 10

            settings.PLATFORM_SSO_BASE_URL = ''
            before_missing = remote_requests
            assert (await client.get('/whoami', headers=headers)).status_code == 503
            assert remote_requests == before_missing
            settings.PLATFORM_SSO_BASE_URL = 'https://sso.test/api'
            checks += 1

            async with sessions() as db:
                # Only the five deliberately authenticated platform identities
                # and the pre-existing local user may have been provisioned.
                assert await db.scalar(select(func.count()).select_from(User)) == 6
                local = await db.get(User, legacy_id)
                local.is_active = False
                await db.commit()
            assert (await client.get('/whoami', headers={'Authorization': 'Bearer ' + legacy_jwt})).status_code == 401
            assert (await client.get('/whoami', headers={'X-API-Key': 'legacy-test-key'})).status_code == 401
            checks += 1

    http_log.removeHandler(log_handler)
    http_log.setLevel(old_level)
    assert valid not in log_output.getvalue() and forged not in log_output.getvalue()
    assert 'ValidateToken?' in log_output.getvalue() and 'redacted' in log_output.getvalue()
    assert 'http://test/whoami' in log_output.getvalue()  # Other HTTP logs remain intact.
    checks += 1
    await engine.dispose()
    print(f'PASS: {checks} checks; platform SSO validation, stable login identity, claim guards, account/issuer isolation, legacy JWT/API key compatibility, failure closure, HTTP log redaction. In-memory database and mocked network only.')


asyncio.run(main())
