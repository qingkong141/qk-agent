"""Renew a running reader's platform session; credentials never enter task records."""
import time

import httpx
from fastapi import HTTPException
from jose import JWTError, jwt

from app.config import settings
from app.platform_auth import validate_platform_identity


class PlatformSession:
    def __init__(self, token, refresh_token, owner_id):
        self.token = token.removeprefix('Bearer ').strip()
        self.refresh_token = refresh_token
        self.owner_id = owner_id

    async def access_token(self, force=False):
        try:
            expires = float(jwt.get_unverified_claims(self.token).get('exp', 0))
        except (JWTError, TypeError, ValueError):
            expires = 0
        if not force and (not self.refresh_token or expires > time.time() + 60):
            return self.token
        if not self.refresh_token:
            raise ValueError('平台登录凭据已失效，缺少续期凭据，请重新登录后启动任务')
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
                response = await client.post(settings.PLATFORM_SSO_BASE_URL.rstrip('/') + '/SSO/RefreshToken',
                    json={'RefreshToken': self.refresh_token, 'clientSys': settings.PLATFORM_SYSTEM_CODE},
                    headers={'UISystemCode': settings.PLATFORM_SYSTEM_CODE})
            if response.status_code in (401, 403):
                raise ValueError('平台会话已失效，自动续期失败，请重新登录后启动任务')
            response.raise_for_status()
            body = response.json()
            data = body.get('Content') if isinstance(body, dict) and body.get('Status') == 1 else None
            if not isinstance(data, dict) or not isinstance(data.get('access_token'), str) or not data['access_token'].strip():
                raise ValueError('平台会话已失效，自动续期失败，请重新登录后启动任务')
            token = data['access_token'].removeprefix('Bearer ').strip()
            if await validate_platform_identity(token) != self.owner_id:
                raise ValueError('续期凭据与任务账号不一致，请重新登录后启动任务')
        except HTTPException:
            raise ValueError('平台续期身份校验失败，请重新登录后启动任务') from None
        except httpx.HTTPError:
            raise ValueError('平台登录续期服务暂不可用，请检查SSO服务后重新启动任务') from None
        self.token = token
        if isinstance(data.get('refresh_token'), str) and data['refresh_token'].strip():
            self.refresh_token = data['refresh_token']
        return self.token
