"""Read-only SSO checks inside aiot-agent; never print credentials or tokens."""
import argparse
import getpass
import json
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone

import httpx
from jose import jwt

sys.path.insert(0, '/app' if Path('/app/app').is_dir() else str(Path(__file__).resolve().parents[2] / 'backend'))
from app.config import settings


def report(label, response, elapsed, token=''):
    summary = {'step': label, 'http': response.status_code, 'ms': round(elapsed * 1000)}
    try:
        body = response.json()
        if isinstance(body, dict):
            for key in ('Status', 'Code', 'Message', 'detail'):
                value = body.get(key)
                if isinstance(value, (str, int, bool)):
                    if isinstance(value, str):
                        if token: value = value.replace(token, '[redacted]')
                        value = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[redacted]', value)
                    summary[key] = str(value)[:200]
        elif isinstance(body, list):
            summary['records'] = len(body)
    except ValueError:
        summary['format'] = 'non-JSON'
        summary['content_type'] = response.headers.get('content-type', '')[:100]
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description='诊断真实登录令牌，不导入或修改业务数据')
    parser.add_argument('--account', default='admin')
    args = parser.parse_args()
    base = settings.PLATFORM_SSO_BASE_URL.rstrip('/')
    print('SSO:', base, 'UISystemCode:', settings.PLATFORM_SYSTEM_CODE, flush=True)
    print('UTC:', datetime.now(timezone.utc).isoformat(), flush=True)
    password = getpass.getpass('请输入平台密码（不显示）：')
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        headers = {'UISystemCode': settings.PLATFORM_SYSTEM_CODE}
        started = time.monotonic()
        response = client.post(base + '/SSO/Login', headers=headers,
            json={'account': args.account, 'password': password, 'isLocal': True})
        report('login', response, time.monotonic() - started)
        body = response.json()
        if response.status_code != 200 or body.get('Status') != 1:
            return 1
        content = body['Content']
        if isinstance(content, str): content = json.loads(content)
        token = content['Result']['access_token']
        claims = jwt.get_unverified_claims(token)
        print('令牌剩余秒数:', round(float(claims.get('exp', 0)) - time.time()),
            '生效时间差（秒）:', round(float(claims.get('nbf', 0)) - time.time()), flush=True)
        for attempt in range(1, 13):
            for label, method, url, options in [
                ('SSO', 'POST', base + '/SSO/ValidateToken', {'params': {'token': token}, 'headers': headers}),
                ('Agent', 'GET', 'http://127.0.0.1:8000/api/v1/studio/artifacts', {'headers': {'X-Platform-Token': token}}),
            ]:
                started = time.monotonic()
                try:
                    response = client.request(method, url, **options)
                    report(f'{attempt}/{label}', response, time.monotonic() - started, token)
                except httpx.HTTPError as error:
                    print(f'{attempt}/{label}: {type(error).__name__}, {time.monotonic()-started:.2f}s', flush=True)
                    return 1
            time.sleep(1)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (httpx.HTTPError, ValueError, KeyError) as error:
        print('诊断停止：' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
