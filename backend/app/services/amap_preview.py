"""Render a map using the owning account's saved Amap Web service credential."""
import base64

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services import mcp_registry as registry


class MapInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    zoom: int = Field(default=15, ge=1, le=17)


async def render(service_id, data, db, user):
    item = await registry.owned(service_id, db, user)
    if not item.enabled: raise HTTPException(409, '地图服务已停用')
    if item.url.rstrip('/') != 'https://mcp.amap.com/mcp' or item.auth_type != 'query' or item.query_name != 'key':
        raise HTTPException(400, '地图预览需要配置高德官方MCP及Web服务Key')
    try: key = registry.cipher().decrypt(item.credential.encode()).decode()
    except InvalidToken as exc: raise HTTPException(409, '地图密钥无法解密，请重新配置') from exc
    location = f'{data.longitude:.6f},{data.latitude:.6f}'
    params = {'location':location, 'zoom':data.zoom, 'size':'800*460', 'markers':f'mid,0xB38B61,A:{location}'}
    try:
        # Fixed provider endpoint; no platform token, cookie or key in browser URLs/logs.
        async with httpx.AsyncClient(transport=registry.QueryCredentialTransport('key', key), timeout=20, trust_env=False, follow_redirects=False) as client:
            response = await client.get('https://restapi.amap.com/v3/staticmap', params=params)
    except httpx.HTTPError as exc: raise HTTPException(502, '地图加载失败，请稍后重试') from exc
    if response.status_code != 200 or not response.content.startswith(b'\x89PNG\r\n\x1a\n'):
        raise HTTPException(502, '高德未返回地图，请检查Web服务Key的静态地图权限或配额')
    return {'image':'data:image/png;base64,'+base64.b64encode(response.content).decode()}
