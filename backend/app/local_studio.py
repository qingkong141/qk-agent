"""Explicit loopback-only development access for studio pages while SSO is offline."""
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from app.config import settings

LOCAL_STUDIO_TOKEN = "local-studio-development"


def local_studio_principal(request: Request | None, token: str | None):
    if token != LOCAL_STUDIO_TOKEN:
        return None
    local_hosts = {"127.0.0.1", "::1", "localhost"}
    prefix = settings.API_V1_PREFIX
    allowed_path = request and any(request.url.path == prefix + path or request.url.path.startswith(prefix + path + "/") for path in ("/studio", "/datasets"))
    origin = request.headers.get("origin") if request else None
    if not (settings.DEBUG and settings.LOCAL_STUDIO_NO_LOGIN and request and request.client
            and request.client.host in local_hosts and allowed_path
            and (not origin or urlsplit(origin).hostname in local_hosts)):
        raise HTTPException(401, "本地工作台免登录未启用或请求不在本机开发范围内")
    return {"id": "00000000-0000-4000-8000-000000000017", "email": "local-studio@localhost",
            "auth_type": "local_studio", "workspace": "local", "external_user_id": ""}
