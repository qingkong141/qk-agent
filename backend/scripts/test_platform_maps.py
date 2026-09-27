"""PM/PE SDK contract, token ownership and restrictive map permissions (mock transport)."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import HTTPException
from jose import jwt
from app.services import platform_maps as maps


async def main():
    issued=lambda user,expiry:jwt.encode({'userID':user,'iss':'test-sso','exp':expiry},'contract-test',algorithm='HS256')
    host=issued('admin',time.time()+3600);position=issued('admin',time.time()+3600)
    exchange=position;permissions='1,2';calls=[]
    def response(request):
        calls.append(request)
        if request.url.path.endswith('/GetTokenByID'):
            assert request.url.params['sid']==headers['X-Platform-Session-ID']
            body=json.dumps({'Result':{'access_token':exchange,'refresh_token':'refresh'}})
        elif request.url.path.endswith('/RefreshToken'):
            assert json.loads(request.content)['clientSys']=='POSITION';body={'access_token':position}
        elif request.url.path.endswith('/ValidateToken'):body=True
        elif request.url.path.endswith('/GetUserAllowMaps'):
            assert request.headers['UISystemCode']=='POSITION';body=permissions
        elif request.url.path.endswith('/GetStructure'):
            assert request.url.params['mapIDs']=='1,2';body=[{'id':1,'name':'输液室'}]
        else:
            assert json.loads(request.content)=={'MapID':2,'LoadOffline':True,'TerminalIDs':'INF-001'};body=[{'TerminalID':'INF-001','X':1,'Y':2}]
        return httpx.Response(200,json={'Status':1,'Content':body})
    original=httpx.AsyncClient
    def client(**kwargs):return original(transport=httpx.MockTransport(response),**kwargs)
    with patch.object(maps.settings,'PLATFORM_SSO_BASE_URL','http://sso/api'),patch.object(maps.settings,'PLATFORM_PM_BASE_URL','http://pm/api'),patch.object(maps.settings,'PLATFORM_PE_BASE_URL','http://pe/api'),patch.object(maps.httpx,'AsyncClient',client):
        headers={'X-Platform-Token':host,'X-Platform-Session-ID':'session-001'};user={'auth_type':'platform'}
        result=await maps.read_map(headers,user);assert result['structure'][0]['id']==1
        headers['X-Platform-Session-ID']='encoded%2Bsession%2Fid%3D'
        result=await maps.read_map(headers,user);assert result['structure'][0]['id']==1
        result=await maps.read_map(headers,user,2,'INF-001');assert result['positions'][0]['X']==1
        async def denied(code,**kwargs):
            try:await maps.read_map(headers,user,**kwargs)
            except HTTPException as e:assert e.status_code==code,e.detail
            else:raise AssertionError('expected denial')
        await denied(403,map_id=3)
        for invalid in ['invalid%ZZ','line\nbreak','x'*501]:
            headers['X-Platform-Session-ID']=invalid
            await denied(400)
        headers['X-Platform-Session-ID']='session-001'
        permissions='';await denied(403)
        permissions='1,2';exchange=issued('another-user',time.time()+3600);calls.clear();await denied(403);assert not any('/GetStructure' in str(r.url) for r in calls)
        exchange=issued('admin',time.time()-10);calls.clear();result=await maps.read_map(headers,user);assert any('/SSO/RefreshToken' in str(r.url) for r in calls)
    print('PASS: SDK exchange and refresh, verified identity binding, restricted maps, coordinates, empty-permission denial. Remote services not contacted.')


asyncio.run(main())
