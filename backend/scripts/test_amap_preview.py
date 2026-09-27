"""Map proxy validation without calling a paid provider or modifying platform data."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI, HTTPException
from app.api import mcp_services
from app.dependencies import get_current_user, get_db
from app.services import amap_preview as maps


async def main():
    app=FastAPI();app.include_router(mcp_services.router)
    app.dependency_overrides[get_current_user]=lambda:{'id':'map-user'}
    app.dependency_overrides[get_db]=lambda:None
    with patch.object(maps.registry.settings,'SECRET_KEY','isolated-map-test'):
        item=SimpleNamespace(enabled=True,url='https://mcp.amap.com/mcp',auth_type='query',query_name='key',credential=maps.registry.cipher().encrypt(b'map-test-secret').decode())
        owned=AsyncMock(return_value=item)
        calls=[]
        async def wire(request):
            calls.append(request)
            assert request.url.host=='restapi.amap.com'
            assert request.url.params['key']=='map-test-secret'
            assert not any(h in request.headers for h in ['x-platform-token','cookie','authorization'])
            return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nimage')
        original=maps.registry.QueryCredentialTransport
        with patch.object(maps.registry,'owned',owned),patch.object(maps.registry,'QueryCredentialTransport',side_effect=lambda name,key:original(name,key,httpx.MockTransport(wire))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                url='/studio/mcp-services/mcp:test/map-preview'
                payload={'longitude':106.5,'latitude':29.5,'zoom':15}
                response=await client.post(url,json=payload)
                assert response.status_code==200 and response.json()['image'].startswith('data:image/png;base64,')
                assert 'map-test-secret' not in response.text
                assert calls[-1].url.params['markers'].endswith('106.500000,29.500000')
                for field,value in [('longitude',181),('latitude',91),('zoom',18)]:
                    assert (await client.post(url,json={**payload,field:value})).status_code==422
                owned.side_effect=HTTPException(404,'MCP服务不存在')
                assert (await client.post(url,json=payload)).status_code==404
                owned.side_effect=None
                item.enabled=False
                assert (await client.post(url,json=payload)).status_code==409
                item.enabled=True;item.url='https://other.example/mcp'
                assert (await client.post(url,json=payload)).status_code==400
                assert len(calls)==1
                item.url='https://mcp.amap.com/mcp'
                with patch.object(httpx.AsyncClient,'get',AsyncMock(return_value=httpx.Response(200,json={'info':'INVALID_USER_KEY'}))):
                    response=await client.post(url,json=payload)
                    assert response.status_code==502 and 'INVALID_USER_KEY' not in response.text
    print('PASS: map image response, coordinate validation, owned/enabled Amap-only service, credential isolation and provider errors')

if __name__=='__main__':asyncio.run(main())
