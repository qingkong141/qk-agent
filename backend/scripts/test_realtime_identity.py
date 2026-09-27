"""A directory MAC must find its case-variant Influx records, without widening other IDs."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import realtime_poll as poll


async def main():
    original = httpx.AsyncClient
    observed = []
    def respond(request):
        observed.append(request.url.params['searchScript'])
        rows = [{'deviceId': code, 'time': '2026-09-27T10:00:00Z', 'value': {'app': [{'identifier': 'infusion', 'data': [{'type': 'charging', 'values': [{'key': 'Voltage', 'value': 4100}]}]}]}} for code in ['546c50350026', '546C50350026', '546c50350027', 'INF-001', 'inf-001']]
        rows[0]['time'] = '09/27/2026 18:01:00'
        return httpx.Response(200, json={'Status': 1, 'Content': {'count': len(rows), 'list': rows}})
    with patch.object(poll.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond))), patch.object(poll.settings, 'PLATFORM_DEVICE_BASE_URL', 'http://platform/api'):
        source = {'table': 'm_infusion', 'lookback_minutes': 10, 'metric': 'metric.infusion.charging.Voltage', 'device_id': '546C50350026'}
        result = await poll.fetch(source, 'test-token')
        assert [r['deviceId'] for r in result] == ['546C50350026', '546c50350026']
        assert result[-1]['time'] == '2026-09-27T10:01:00+00:00'
        assert "deviceId='546C50350026'" in observed[-1] and "deviceId='546c50350026'" in observed[-1]
        result = await poll.fetch({**source, 'device_id': 'INF-001'}, 'test-token')
        assert [r['deviceId'] for r in result] == ['INF-001']
        assert observed[-1] == "deviceId='INF-001'"
    print('PASS: MAC identity, isolated ordinary IDs, legacy time normalization and latest-point ordering')


asyncio.run(main())
