"""Actual Node execution, version hot swapping, failed activation and downloadable package."""
import asyncio
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import plugins as api
from app.db import session as database_module
from app.db.session import Base,get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact
from app.models.protocol_plugin import PluginVersion
from app.services import plugin_runtime as runtime


def config(scale=1):
    fields=[{'key':'deviceId','name':'设备编号','type':'string','unit':''},{'key':'battery','name':'剩余电量','type':'number','unit':'%','minimum':0,'maximum':100}]
    return {'name':'输液协议','productKey':'P1','inputFormat':'json','raw':json.dumps({'deviceId':'INF-1','productKey':'P1','battery':61}),
        'parametersText':'{}','syntax':{'name':'报文提取','script':'payload'},'semantic':{'name':'标准字段','mode':'visual','script':'',
            'sourceModel':{'name':'source','fields':fields},'targetModel':{'name':'target','fields':fields},
            'mappings':[{'source':f['key'],'target':f['key'],'kind':'scale' if f['key']=='battery' else 'copy','scale':scale,'offset':0,'fallback':'','enumText':'{}'} for f in fields]}}


async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False);principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def user():return principal
    async def database():
        async with sessions() as db:yield db
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_current_user]=user;app.dependency_overrides[get_db]=database
    async with sessions() as db:
        for i,c in [('debug1',config()),('debug2',config(.5)),('bad',config(10))]:db.add(StudioArtifact(id=i,name=i,kind='protocol_debug',owner_id='owner',external_user_id='',revision=1,config=c))
        await db.commit()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            base='/studio/plugins';packet={'raw':config()['raw']}
            r=await client.post(base,json={'name':'输液协议','debug_id':'debug1'});assert r.status_code==201,r.text
            item=r.json();path=base+'/'+item['id'];detail=(await client.get(path)).json();v1=detail['versions'][0]['id'];core=detail['core']['instance_id']
            assert (await client.post(path+'/ingest',json=packet)).status_code==409
            r=await client.post(path+'/start',json={'version_id':v1,'expected_revision':1});assert r.status_code==200,r.text
            pid1=runtime.registry[item['id']].process.pid
            r=await client.post(path+'/ingest',json=packet);assert r.json()['output']['battery']==61 and r.json()['version_id']==v1
            r=await client.post(path+'/versions',json={'debug_id':'bad','expected_revision':2});assert r.status_code==422,r.text
            assert (await client.get(path)).json()['revision']==2
            assert (await client.post(path+'/ingest',json=packet)).json()['output']['battery']==61
            r=await client.post(path+'/versions',json={'debug_id':'debug2','expected_revision':2});assert r.status_code==201,r.text;v2=r.json()['id']
            assert (await client.post(path+'/ingest',json=packet)).json()['output']['battery']==61
            r=await client.post(path+'/start',json={'version_id':v2,'expected_revision':3});assert r.status_code==200 and r.json()['core']['instance_id']==core,r.text
            assert runtime.registry[item['id']].process.pid!=pid1
            r=await client.post(path+'/ingest',json=packet);assert r.json()['output']['battery']==30.5 and r.json()['version_id']==v2
            assert (await client.post(path+'/ingest',json={'raw':'{invalid'})).status_code==422
            assert (await client.post(path+'/ingest',json={'raw':json.dumps({'productKey':'P1','battery':0})})).json()['output']=={'deviceId':None,'battery':0}
            assert (await client.post(path+'/start',json={'version_id':v1,'expected_revision':3})).status_code==409
            assert runtime.registry[item['id']].version_id==v2
            download=await client.get(path+f'/versions/{v2}/download');assert download.status_code==200,download.text
            with tempfile.TemporaryDirectory() as directory:
                with zipfile.ZipFile(io.BytesIO(download.content)) as archive:archive.extractall(directory)
                p=await asyncio.create_subprocess_exec('node','run.cjs',cwd=directory,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                out,err=await asyncio.wait_for(p.communicate((json.dumps(packet)+'\n').encode()),10)
                rows=[json.loads(line) for line in out.splitlines()];assert p.returncode==0 and rows[0]['ready'] and rows[1]['output']['battery']==30.5,err
            assert (await client.post(path+'/start',json={'version_id':v1,'expected_revision':4})).status_code==200
            assert (await client.post(path+'/ingest',json=packet)).json()['output']['battery']==61
            await runtime.shutdown()
            with patch.object(database_module,'async_session',sessions):await runtime.startup()
            assert (await client.post(path+'/ingest',json=packet)).json()['output']['battery']==61
            principal['id']='other';assert (await client.get(path)).status_code==404;assert (await client.get(base)).json()['items']==[];principal['id']='owner'
            history=(await client.get(path+'/messages')).json();assert any(not i['ok'] for i in history) and {i['version_id'] for i in history}=={v1,v2}
            assert (await client.request('DELETE',path,json={'expected_revision':5})).status_code==409
            assert (await client.post(path+'/stop',json={'expected_revision':5})).status_code==200
            assert (await client.post(path+'/ingest',json=packet)).status_code==409
            assert (await client.request('DELETE',path,json={'expected_revision':6})).status_code==200
    finally:await runtime.shutdown();await engine.dispose()
    print('PASS: actual Node execution, immutable versions, hot swap, rollback, failed version isolation, restart recovery, standalone ZIP, messages and ownership')


asyncio.run(main())
