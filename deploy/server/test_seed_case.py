"""Run with backend/venv/Scripts/python.exe deploy/server/test_seed_case.py."""
import sys
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from jose import jwt
from seed_case import API, CASE_ID, LocalAPI, local_owner, settings

AUTH_UNAVAILABLE = '平台登录校验服务暂不可用，请稍后重试'


def response(status, body):
    return httpx.Response(status, json=body)


class SeedRetryTest(unittest.TestCase):
    def setUp(self):
        self.api = API.__new__(API)
        self.api.base = 'http://agent.invalid/api/v1'
        self.api.token = 'test-token'
        self.api.client = Mock()
        self.api.login = Mock()
        self.sleep = patch('seed_case.time.sleep').start()
        self.addCleanup(patch.stopall)
        self.payload = {'name': 'test-service', 'rows': []}

    def call(self):
        return self.api.call('POST', '/studio/data-services', json=self.payload)

    def test_recovers_from_validation_503_and_preserves_request(self):
        self.api.client.request.side_effect = [response(503, {'detail': AUTH_UNAVAILABLE}),
            response(503, {'detail': AUTH_UNAVAILABLE}), response(201, {'id': 'created'})]
        self.assertEqual(self.call(), {'id': 'created'})
        self.assertEqual(self.api.client.request.call_count, 3)
        for call in self.api.client.request.call_args_list:
            self.assertEqual(call.kwargs['json'], self.payload)
            self.assertEqual(call.kwargs['headers'], {'X-Platform-Token': 'test-token'})
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 4])
        self.api.login.assert_not_called()

    def test_stops_after_three_validation_failures(self):
        self.api.client.request.return_value = response(503, {'detail': AUTH_UNAVAILABLE})
        with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
            self.call()
        self.assertEqual(self.api.client.request.call_count, 3)

    def test_renews_expired_token_after_transient_validation_failure(self):
        self.api.client.request.side_effect = [response(503, {'detail': AUTH_UNAVAILABLE}),
            response(401, {'detail': 'expired'}), response(201, {'id': 'created'})]
        self.api.login.side_effect = lambda: setattr(self.api, 'token', 'renewed-token')
        self.assertEqual(self.call(), {'id': 'created'})
        self.api.login.assert_called_once()
        self.assertEqual(self.api.client.request.call_args.kwargs['headers'], {'X-Platform-Token': 'renewed-token'})

    def test_does_not_replay_unknown_server_errors_or_denied_requests(self):
        for status in (400, 403, 409, 500, 502, 503):
            with self.subTest(status=status):
                self.api.client.request.reset_mock()
                self.api.client.request.return_value = response(status, {'detail': 'other failure'})
                with self.assertRaises(RuntimeError):
                    self.call()
                self.api.client.request.assert_called_once()

    def test_does_not_replay_write_when_response_is_lost(self):
        self.api.client.request.side_effect = httpx.ReadTimeout('response lost')
        with self.assertRaises(httpx.ReadTimeout):
            self.call()
        self.api.client.request.assert_called_once()


class LocalImportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.owner = str(uuid.uuid4())
        self.sso = patch.object(settings, 'PLATFORM_SSO_BASE_URL', 'http://target.test/api').start()
        self.addCleanup(patch.stopall)

    def progress(self, owner, sso='http://target.test/api'):
        path = self.directory / f'{CASE_ID}-{owner}.json'
        path.write_text(json.dumps({'case_id': CASE_ID, 'owner': owner, 'sso': sso}), encoding='utf-8')

    def test_requires_verified_scope_and_keeps_environments_separate(self):
        self.progress(self.owner, 'http://other.test/api')
        with self.assertRaisesRegex(RuntimeError, '未找到'):
            local_owner(self.directory)
        self.progress(self.owner)
        self.assertEqual(local_owner(self.directory), self.owner)

    def test_multiple_accounts_need_explicit_owner(self):
        self.progress(self.owner)
        other = str(uuid.uuid4())
        self.progress(other)
        with self.assertRaisesRegex(RuntimeError, '多个账号'):
            local_owner(self.directory)
        self.assertEqual(local_owner(self.directory, self.owner), self.owner)

    def test_does_not_send_admin_token_to_remote_hosts(self):
        for base in ('http://192.168.10.249:8000/api/v1', 'http://127.0.0.1@evil.test/api/v1'):
            with self.assertRaisesRegex(RuntimeError, '回环地址'):
                LocalAPI(base, self.owner)

    def test_local_credential_is_scoped_short_lived_and_skips_sso(self):
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(is_active=True)
        context = AsyncMock()
        context.__aenter__.return_value = db
        api = LocalAPI('http://127.0.0.1:8000/api/v1', self.owner)
        self.addCleanup(api.client.close)
        with patch('app.db.session.async_session', return_value=context), patch.object(settings, 'SECRET_KEY', 'test-only-local-key'):
            with patch.object(api.client, 'post', side_effect=AssertionError('Must not contact SSO')):
                api.login()
            claims = jwt.decode(api.token, settings.SECRET_KEY, algorithms=['HS256'])
            self.assertEqual(claims['sub'], self.owner)
            self.assertLessEqual(claims['exp'] - time.time(), 900)
            self.assertGreater(claims['exp'] - time.time(), 895)
            self.assertEqual(api.auth_headers(), {'Authorization': 'Bearer ' + api.token})
            db.get.return_value = SimpleNamespace(is_active=False)
            with self.assertRaisesRegex(RuntimeError, '停用'):
                api.login()
            db.get.return_value = None
            with self.assertRaisesRegex(RuntimeError, '不存在'):
                api.login()


if __name__ == '__main__':
    unittest.main()
