"""Run with backend/venv/Scripts/python.exe deploy/server/test_seed_case.py."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from seed_case import API

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


if __name__ == '__main__':
    unittest.main()
