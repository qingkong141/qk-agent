"""Isolated application data binding and publication lifecycle tests."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import applications,studio
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact
from app.models.publication import PublicationAccess
from app.models.realtime import RealtimeTask
from app.services.application_schema import Binding,Widget,ApplicationConfig
from app.services import realtime_poll

async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(StudioArtifact.__table__.create);await conn.run_sync(RealtimeTask.__table__.create)
        await conn.run_sync(PublicationAccess.__table__.create)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def database():
        async with sessions() as db: yield db
    async def user(): return principal
    app=FastAPI();app.include_router(applications.router);app.include_router(studio.router)
    app.dependency_overrides[get_db]=database;app.dependency_overrides[get_current_user]=user
    async with sessions() as db:
        for ident,owner in [('service','owner'),('other','other')]:
            db.add(StudioArtifact(id=ident,owner_id=owner,external_user_id='',name='设备读数',kind='data_service',config={'rows':[{'deviceId':'A','time':'2026-09-27','value':61},{'deviceId':'B','time':'2026-09-27','value':0}],'fields':['deviceId','time','value'],'enabled':True,'require_login':False,'captured_at':'2026-09-27T00:00:00Z'}))
        db.add(RealtimeTask(id='rt',owner_id='owner',external_user_id='',name='实时数据',config={},status='stopped',runtime={'rows':[{'value':55}],'processed_at':'2026-09-27T00:00:00Z'}))
        await db.commit()
    cfg=ApplicationConfig(schemaVersion=2,title='设备概览',source=Binding(kind='data_service',id='service'),widgets=[Widget(id='metric',kind='metric',title='剩余电量',field='value'),Widget(id='table',kind='table',title='读数',columns=['deviceId','value'])]).model_dump()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        base='/studio/applications'
        assert (await client.post(base+'/data',json={'kind':'data_service','id':'other'})).status_code==404
        valid=await client.post(base+'/debug',json=cfg);assert valid.status_code==200,valid.text
        assert valid.json()['rows'][1]['value']==0 and len(valid.json()['checks'])==2
        bad={**cfg,'widgets':[{**cfg['widgets'][0],'field':'missing'}]}
        assert (await client.post(base+'/debug',json=bad)).status_code==422
        strings={**cfg,'source':{'kind':'snapshot','rows':[{'value':'61','deviceId':'A'}]}}
        assert (await client.post(base+'/debug',json=strings)).status_code==422
        for value, text in [(False,'false'),(None,'')]:
            filtered={**cfg,'source':{'kind':'snapshot','rows':[{'value':'bad','flag':value}]},'widgets':[{**cfg['widgets'][0],'filterField':'flag','filterValue':text}]}
            assert (await client.post(base+'/debug',json=filtered)).status_code==422
        assert (await client.post(base+'/data',json={'kind':'snapshot','rows':[{'value':False},{'value':None}]})).status_code==200
        assert (await client.post(base+'/debug',json={**cfg,'refreshSeconds':2})).status_code==422
        logic={**cfg,'mode':'logic','logic':[{'id':'rule','field':'value','compare':'lt','value':40,'output':'condition','whenTrue':'需关注','whenFalse':'阈值内'}],
               'source':{'kind':'snapshot','rows':[{'deviceId':'A','value':None},{'deviceId':'B','value':0},{'deviceId':'C','value':61}]}}
        evaluated=await client.post(base+'/debug',json=logic);assert evaluated.status_code==200,evaluated.text
        assert [r['condition'] for r in evaluated.json()['rows']]==[None,'需关注','阈值内']
        bad_logic={**logic,'logic':[{**logic['logic'][0],'output':'value'}]}
        assert (await client.post(base+'/debug',json=bad_logic)).status_code==422
        for mode in ['iot','screen','mobile','scada','logic','twin']:
            assert (await client.post(base+'/debug',json={**cfg,'mode':mode})).status_code==200
        scene={'nodes':[{'id':'a','name':'设备A','deviceId':'A','kind':'device','x':20,'y':20},{'id':'b','name':'设备B','deviceId':'B','kind':'tank','x':70,'y':60}], 'edges':[{'from':'a','to':'b'}],'alertBelow':40}
        scene_widget={**cfg['widgets'][0],'kind':'twin','groupField':'deviceId','scene':scene}
        assert (await client.post(base+'/debug',json={**cfg,'widgets':[scene_widget]})).status_code==200
        scene_widget['scene']={**scene,'edges':[{'from':'a','to':'missing'}]}
        assert (await client.post(base+'/debug',json={**cfg,'widgets':[scene_widget]})).status_code==422
        assert (await client.post(base+'/debug',json={**cfg,'widgets':[cfg['widgets'][0],cfg['widgets'][0]]})).status_code==422
        page_widget={**cfg['widgets'][0],'id':'details-metric','filterField':'deviceId','filterVariable':'device'}
        table={**cfg['widgets'][1],'action':{'kind':'navigate','page':'details','variable':'device','valueField':'deviceId'}}
        multi={**cfg,'widgets':[table],'variables':[{'name':'device','label':'设备','defaultValue':''}],
               'pages':[{'id':'details','title':'详情','widgets':[page_widget]}]}
        good=await client.post(base+'/debug',json=multi);assert good.status_code==200,good.text
        assert len(good.json()['checks'])==2
        assert (await client.post(base+'/debug',json={**multi,'variables':[]})).status_code==422
        assert (await client.post(base+'/debug',json={**multi,'pages':[]})).status_code==422
        invalid_page={**multi,'pages':[{'id':'details','title':'详情','widgets':[{**page_widget,'field':'missing'}]}]}
        assert (await client.post(base+'/debug',json=invalid_page)).status_code==422
        assert (await client.post(base+'/debug',json={**multi,'variables':[{'name':'constructor','label':'错误','defaultValue':''}]})).status_code==422
        rt=(await client.post(base+'/data',json={'kind':'realtime','id':'rt'})).json()
        assert rt['source_status']=='已停止' and rt['rows'][0]['value']==55
        created=await client.post('/studio/artifacts',json={'kind':'application','name':'设备应用','config':cfg});assert created.status_code==201,created.text
        item=created.json();path='/studio/artifacts/'+item['id']
        assert (await client.post(path+'/publish',json={'expected_revision':1})).status_code==200
        duplicate=await client.post(path+'/publish',json={'expected_revision':1})
        assert duplicate.status_code==409 and '先停用' in duplicate.json()['detail']
        assert (await client.request('DELETE',path,json={'expected_revision':1})).status_code==409
        draft={**cfg,'title':'新草稿'}
        assert (await client.put(path,json={'kind':'application','name':'设备应用','config':draft,'expected_revision':1})).status_code==200
        assert (await client.post(path+'/publish',json={'expected_revision':2})).status_code==409
        published=(await client.get(path+'/published')).json()
        assert published['revision']==1 and published['config']['title']=='设备概览'
        async with sessions() as db:
            service=await db.get(StudioArtifact,'service');service.config={**service.config,'rows':[{'deviceId':'A','time':'2026-09-27','value':88}]};await db.commit()
        # Published data is read from the current source, not from the saved page snapshot.
        assert (await client.post(base+'/debug',json=published['config'])).json()['rows'][0]['value']==88
        assert (await client.post(path+'/publish',json={'expected_revision':1})).status_code==409
        async with sessions() as db:
            service=await db.get(StudioArtifact,'service');service.config={**service.config,'enabled':False};await db.commit()
        assert (await client.post(base+'/data',json=cfg['source'])).status_code==409
        assert (await client.post(path+'/publish',json={'expected_revision':2})).status_code==409
        principal['id']='other'
        assert (await client.get(path+'/published')).status_code==404
        assert (await client.post(base+'/data',json={'kind':'realtime','id':'rt'})).status_code==404
        principal.update(id='owner',auth_type='api_key',external_user_id='other-user')
        assert (await client.post(base+'/data',json=cfg['source'])).status_code==404
        principal.update(auth_type='jwt',external_user_id='')
        assert (await client.post(path+'/unpublish',json={'expected_revision':2})).status_code==200
        assert (await client.get(path+'/published')).status_code==404
        assert (await client.request('DELETE',path,json={'expected_revision':1})).status_code==409
        assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==200
        rows=[{'value':i} for i in range(1001)]
        bounded=(await client.post(base+'/data',json={'kind':'snapshot','rows':rows})).json()
        assert bounded['total']==1001 and bounded['truncated'] and len(bounded['rows'])==1000
        async def fetch(source,token):return [{'deviceId':'A','time':'2026-09-27T00:00:00Z','value':61}]
        with patch.object(realtime_poll,'fetch',fetch),patch('app.config.settings.PLATFORM_DEVICE_BASE_URL','http://test'):
            assert (await client.post(base+'/data',json={'kind':'platform'})).status_code==400
            platform=await client.post(base+'/data',json={'kind':'platform'},headers={'X-Platform-Token':'test'})
            assert platform.json()['rows'][0]['value']==61
        # Keep the old application snapshot API usable.
        legacy={'title':'旧应用','source':'页面数据','rows':[{'value':0}],'widgets':[{'id':'old','kind':'metric','title':'读数','field':'value','color':'#b18a61'}]}
        old=(await client.post('/studio/artifacts',json={'kind':'application','name':'旧应用','config':legacy})).json()
        assert (await client.post('/studio/artifacts/'+old['id']+'/publish',json={'expected_revision':1})).status_code==200
    await engine.dispose();print('PASS: owned application bindings, current data, field checks, publication isolation, stop/delete, legacy snapshots')

asyncio.run(main())
