"""Verify platform sessions at the configured SSO before deriving an AI identity."""
import json
import logging
import math
import secrets
import time
import uuid

import bcrypt
import httpx
from fastapi import HTTPException
from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.user import User


class _HideValidationToken(logging.Filter):
    def filter(self, record):
        # This legacy SSO accepts the token in its query string. Keep HTTPX's
        # request log useful without copying a session credential into it.
        if isinstance(record.args, tuple):
            record.args = tuple(
                value.copy_set_param('token', '[redacted]')
                if isinstance(value, httpx.URL) and value.path.endswith('/SSO/ValidateToken') and 'token' in value.params
                else value for value in record.args
            )
        return True


logging.getLogger('httpx').addFilter(_HideValidationToken())


async def validate_platform_identity(token: str) -> str:
    base = settings.PLATFORM_SSO_BASE_URL.rstrip('/')
    if not base:
        raise HTTPException(503, '尚未配置平台SSO地址，请联系管理员')
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            response = await client.post(base + '/SSO/ValidateToken', params={'token': token},
                                         headers={'UISystemCode': settings.PLATFORM_SYSTEM_CODE})
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, '平台登录校验服务暂不可用，请稍后重试') from None
    if not isinstance(result, dict) or type(result.get('Status')) is not int or result['Status'] != 1:
        raise HTTPException(401, '平台登录已失效，请重新登录当前后台')
    # Signature verification is performed by SSO above, not by the browser.
    try:
        claims = jwt.get_unverified_claims(token)
        identity = claims.get('userID')
        issuer = claims.get('iss')
        if not isinstance(identity, (str, int)) or isinstance(identity, bool) or not str(identity).strip():
            raise ValueError()
        if not isinstance(issuer, str) or not issuer.strip():
            raise ValueError()
        expires = float(claims.get('exp', 0))
        valid_from = float(claims.get('nbf', 0))
        now = time.time()
        # SSO-issued tokens can start slightly ahead of the local host clock.
        # Keep expiry strict and allow only a small skew on the not-before time.
        if not math.isfinite(expires) or not math.isfinite(valid_from) or expires <= now or valid_from > now + 30:
            raise ValueError()
    except (JWTError, ValueError, TypeError):
        raise HTTPException(401, '平台令牌缺少有效身份或已过期，请重新登录') from None
    # OrgTid changes on every login in this SSO; it is not a stable tenant ID.
    # Namespace the verified user by the configured SSO and token issuer instead.
    subject = json.dumps([base, issuer, str(identity)], ensure_ascii=False, separators=(',', ':'))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, subject))


async def authenticate_platform(db, token: str) -> dict:
    user_id = await validate_platform_identity(token.removeprefix('Bearer ').strip())
    user = await db.get(User, user_id)
    if user is None:
        # No shared admin, API key, or reusable password is given to the browser.
        user = User(id=user_id, email=f'{user_id}@platform.invalid',
                    hashed_password=bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode(),
                    api_key=None)
        db.add(user)
        try:
            await db.commit()
            await db.refresh(user)
        except IntegrityError:
            await db.rollback()
            user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(403, '当前平台账号的AI服务权限已停用')
    return {'id': user.id, 'email': user.email, 'auth_type': 'platform',
            'workspace': 'default', 'external_user_id': ''}
