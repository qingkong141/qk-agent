"""Read the existing PM/PE map services using the same SSO exchange as pe-map-sdk."""
import json
import logging
import re
import time

import httpx
from fastapi import HTTPException
from jose import jwt
from app.config import settings


class HideMapSession(logging.Filter):
    def filter(self,record):
        if isinstance(record.args,tuple):
            record.args=tuple(v.copy_set_param('sid','[redacted]') if isinstance(v,httpx.URL) and v.path.endswith('/WebSiteConfig/GetTokenByID') else v for v in record.args)
        return True


logging.getLogger('httpx').addFilter(HideMapSession())


def content(response):
    response.raise_for_status()
    if len(response.content)>2_000_000:raise HTTPException(502,'地图响应过大，请缩小查询范围')
    body=response.json()
    if not isinstance(body,dict) or body.get('Status') not in (1,True):raise HTTPException(502,'地图接口拒绝请求，请检查定位服务及账号权限')
    return body.get('Content')


def token_content(value):
    if isinstance(value,str):
        if value.strip().startswith(('{','[')):value=json.loads(value)
        else:return {'access_token':value.strip()}
    if isinstance(value,dict):return value if value.get('access_token') else value.get('Result',{})
    return {}


async def read_map(headers,user,map_id=None,terminal_ids=''):
    if user.get('auth_type')!='platform':raise HTTPException(403,'地图查询需要当前平台账号')
    host_token=headers.get('x-platform-token') or headers.get('X-Platform-Token')
    sid=headers.get('x-platform-session-id') or headers.get('X-Platform-Session-ID')
    if not host_token or not sid:raise HTTPException(401,'缺少平台定位会话，请重新登录后查询地图')
    if len(sid)>200 or not re.fullmatch(r'[A-Za-z0-9_-]+',sid):raise HTTPException(400,'定位会话格式无效')
    if map_id is not None and (type(map_id) is not int or map_id<=0):raise HTTPException(400,'请选择有效地图编号')
    if len(terminal_ids)>1000 or terminal_ids and not re.fullmatch(r'[A-Za-z0-9_, -]+',terminal_ids):raise HTTPException(400,'终端编号格式无效')
    if not settings.PLATFORM_PM_BASE_URL or not settings.PLATFORM_PE_BASE_URL:raise HTTPException(503,'后台尚未配置PM/PE地图服务地址')
    sso=settings.PLATFORM_SSO_BASE_URL.rstrip('/');host_headers={'Authorization':'Bearer '+host_token.removeprefix('Bearer ').strip()}
    try:
        async with httpx.AsyncClient(timeout=10,trust_env=False,follow_redirects=False) as client:
            data=token_content(content(await client.get(sso+'/WebSiteConfig/GetTokenByID',params={'sid':sid},headers=host_headers)))
            token=data.get('access_token','').removeprefix('Bearer ').strip()
            claims=jwt.get_unverified_claims(token)
            if float(claims.get('exp',0))<=time.time()+60 and data.get('refresh_token'):
                data=token_content(content(await client.post(sso+'/SSO/RefreshToken',json={'RefreshToken':data['refresh_token'],'clientSys':'POSITION'},headers=host_headers)))
                token=data.get('access_token','').removeprefix('Bearer ').strip();claims=jwt.get_unverified_claims(token)
            # Validate the exchanged credential and bind it to the already authenticated caller.
            content(await client.post(sso+'/SSO/ValidateToken',params={'token':token},headers={'UISystemCode':'POSITION'}))
            host_claims=jwt.get_unverified_claims(host_token.removeprefix('Bearer ').strip())
            if not claims.get('userID') or str(claims['userID'])!=str(host_claims.get('userID')) or claims.get('iss')!=host_claims.get('iss') or float(claims.get('exp',0))<=time.time():
                raise HTTPException(403,'定位会话与当前账号不一致，请重新登录')
            auth={'Authorization':'Bearer '+token,'UISystemCode':'POSITION','Accept-Language':'zh-CN'}
            permission=content(await client.get(settings.PLATFORM_PM_BASE_URL.rstrip('/')+'/MapPermission/GetUserAllowMaps',params={'mapSysType':settings.PLATFORM_MAP_SYS_TYPE} if settings.PLATFORM_MAP_SYS_TYPE else None,headers=auth))
            permission=str(permission or '').strip()
            if permission!='-1' and not re.fullmatch(r'\d+(?:[,，]\s*\d+)*',permission):raise HTTPException(403,'当前账号没有可用地图权限')
            allowed=None if permission=='-1' else {int(i.strip()) for i in re.split('[,，]',permission) if int(i.strip())>0}
            if allowed==set():raise HTTPException(403,'当前账号没有可用地图权限')
            if map_id is not None and allowed is not None and map_id not in allowed:raise HTTPException(403,'无权查看此地图')
            if map_id is None:
                structure=content(await client.get(settings.PLATFORM_PE_BASE_URL.rstrip('/')+'/Structure/GetStructure',params={'mapIDs':permission},headers=auth))
                if not isinstance(structure,list):raise HTTPException(502,'地图目录响应格式不符合现有SDK契约')
                return {'structure':structure[:100],'truncated':len(structure)>100,'source':'平台定位地图','read_at':time.time()}
            body={'MapID':map_id,'LoadOffline':True}
            if terminal_ids:body['TerminalIDs']=terminal_ids
            positions=content(await client.post(settings.PLATFORM_PE_BASE_URL.rstrip('/')+'/Position/GetCurrentPosition',json=body,headers=auth))
            if not isinstance(positions,list):raise HTTPException(502,'位置响应格式不符合现有SDK契约')
            return {'map_id':map_id,'positions':positions[:500],'truncated':len(positions)>500,'source':'平台定位引擎','read_at':time.time()}
    except HTTPException:raise
    except Exception:raise HTTPException(502,'地图服务连接或定位换票失败，请检查PM/PE服务、SSO会话和网络') from None
