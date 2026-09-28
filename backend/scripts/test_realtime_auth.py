"""Exercise real reader renewal against isolated SSO/device HTTP responses."""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from jose import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import realtime_poll as poll
from app.services.realtime_auth import PlatformSession

BASE = 'http://sso.test/api'
SOURCE = {'table': 'm_infusion', 'device_id': 'INF-001', 'lookback_minutes': 10, 'metric': 'metric.infusion.heartBeat.DevicesPower'}
OWNER = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([BASE, 'platform', 'owner'], separators=(',', ':'))))


def token(user='owner', expires=None, serial=0):
    return jwt.encode({'userID': user, 'iss': 'platform', 'exp': expires or time.time()+3600, 'serial': serial}, 'test-only', algorithm='HS256')


async def main():
    original = httpx.AsyncClient
    state = {}
    def reset(**changes):
        state.clear()
        state.update(refreshes=0, reads=0, statuses=[], refresh_status=200, wrong_user=False, refresh_tokens=[], access_tokens=[])
        state.update(changes)
    def respond(request):
        if request.url.path.endswith('/SSO/RefreshToken'):
            state['refreshes'] += 1
            state['refresh_tokens'].append(json.loads(request.content)['RefreshToken'])
            assert json.loads(request.content)['clientSys'] == poll.settings.PLATFORM_SYSTEM_CODE
            if state['refresh_status'] != 200:
                return httpx.Response(state['refresh_status'])
            return httpx.Response(200, json={'Status': 1, 'Content': {'access_token': token('other' if state['wrong_user'] else 'owner', serial=state['refreshes']), 'refresh_token': f"refresh-{state['refreshes']}"}})
        if request.url.path.endswith('/SSO/ValidateToken'):
            return httpx.Response(200, json={'Status': 1})
        assert request.url.path.endswith('/InfluxDb/GetDatas')
        state['reads'] += 1
        state['access_tokens'].append(request.headers['Authorization'])
        code = state['statuses'].pop(0) if state['statuses'] else 200
        return httpx.Response(code, json={'Status': 1, 'Content': {'count': 0, 'list': []}})
    async def failure(session, expected):
        try:
            await poll.fetch(SOURCE, session)
            raise AssertionError('Expected authentication failure')
        except ValueError as error:
            assert expected in str(error), str(error)

    with patch.object(poll.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond))), \
         patch.object(poll.settings, 'PLATFORM_SSO_BASE_URL', BASE), \
         patch.object(poll.settings, 'PLATFORM_DEVICE_BASE_URL', 'http://device.test/api'):
        reset()
        session = PlatformSession(token(expires=time.time()-1), 'refresh-0', OWNER)
        assert await poll.fetch(SOURCE, session) == []
        assert state['refreshes'] == 1 and state['reads'] == 1
        assert state['access_tokens'][-1] == 'Bearer '+session.token
        await poll.fetch(SOURCE, session)
        assert state['refreshes'] == 1, 'Valid sessions must not renew every poll'
        session.token = token(expires=time.time()-1)
        await poll.fetch(SOURCE, session)
        assert state['refresh_tokens'] == ['refresh-0', 'refresh-1'], 'Keep rotated refresh credentials'

        reset(statuses=[401, 200])
        session = PlatformSession(token(), 'refresh-0', OWNER)
        assert await poll.fetch(SOURCE, session) == []
        assert state['reads'] == 2 and state['refreshes'] == 1
        assert state['access_tokens'][0] != state['access_tokens'][1]

        reset(statuses=[401, 401])
        await failure(PlatformSession(token(), 'refresh-0', OWNER), '登录凭据已失效')
        assert state['reads'] == 2 and state['refreshes'] == 1, 'No retry loop after rejected renewal'

        reset(statuses=[403])
        await failure(PlatformSession(token(), 'refresh-0', OWNER), '没有设备数据读取权限')
        assert state['refreshes'] == 0

        reset(refresh_status=401)
        await failure(PlatformSession(token(expires=time.time()-1), 'refresh-0', OWNER), '自动续期失败')
        assert state['reads'] == 0

        reset(wrong_user=True)
        session = PlatformSession(token(expires=time.time()-1), 'refresh-0', OWNER)
        previous = session.token
        await failure(session, '与任务账号不一致')
        assert state['reads'] == 0 and session.token == previous

        reset(statuses=[401])
        await failure(PlatformSession(token(), None, OWNER), '缺少续期凭据')
        assert state['refreshes'] == 0
    print('PASS: proactive renewal, 401 retry, refresh rotation, owner binding, 403 distinction and bounded auth failure')


asyncio.run(main())
