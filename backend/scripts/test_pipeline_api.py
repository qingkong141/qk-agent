"""Batch pipeline configuration validation and ownership regression with no business database access."""
import asyncio
import copy
import os
import sys
from pathlib import Path
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import studio
from app.models.studio import StudioArtifact
from app.db.session import get_db
from app.dependencies import get_current_user

async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:await conn.run_sync(StudioArtifact.__table__.create)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def db_override():
        async with sessions() as db:yield db
    async def user_override():return principal
    app=FastAPI();app.include_router(studio.router);app.dependency_overrides[get_db]=db_override;app.dependency_overrides[get_current_user]=user_override
    def node(kind,index):return {'id':str(index),'kind':kind,'name':kind,'x':0,'y':0,'field':'battery','value':'20','interval':1,'compare':'gte','valueType':'number','keys':[],'keep':'first','parseMode':'json','script':'{}','conflict':'error','outputField':'condition','trueValue':'yes','falseValue':'no'}
    config={'schemaVersion':2,'nodes':[node('source',0),node('filter',1),node('output',2)],'edges':[{'from':'0','to':'1'},{'from':'1','to':'2'}],'input':'[{"battery":61}]','source':'JSON','chartField':'battery','sourceSettings':{'kind':'influx','query':{'tableName':'m_infusion','startTime':'2026-09-24 00:00:00','endTime':'2026-09-24 23:00:00','searchScript':'','pageIndex':1,'pageSize':20},'deviceIds':['INF-001'],'mode':'metrics'}}
    body={'name':'管道','kind':'pipeline','config':config}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        created=await client.post('/studio/artifacts',json=body);assert created.status_code==201,created.text
        item=created.json();url='/studio/artifacts/'+item['id']
        assert item['config']['sourceSettings']['deviceIds']==['INF-001']
        for mutate in [lambda c:c['edges'].clear(),lambda c:c['edges'].append({'from':'0','to':'2'}),lambda c:c['nodes'][1].update(field=''),lambda c:c.update(input='[1]'),lambda c:c['sourceSettings']['query'].update(endTime='2026-09-23 00:00:00')]:
            bad=copy.deepcopy(config);mutate(bad)
            assert (await client.post('/studio/artifacts',json={**body,'config':bad})).status_code==422
        assert (await client.put(url,json={**body,'expected_revision':1})).status_code==200
        assert (await client.put(url,json={**body,'expected_revision':1})).status_code==409
        # The editor restores empty extraction defaults even for JSON/Influx input.
        extraction={'resource':'','limit':100,'product_field':'productKey','products':[],'lookback_minutes':60}
        for kind in ('json','influx'):
            restored=copy.deepcopy(config)
            restored['sourceSettings'].update(kind=kind,datasource_id='',extraction=extraction)
            payload={**body,'config':restored}
            response=await client.post('/studio/artifacts',json=payload)
            assert response.status_code==201,response.text
            saved=response.json();saved_url='/studio/artifacts/'+saved['id']
            assert saved['config']['sourceSettings']['extraction'] is None
            # Opening a saved record reinstates the UI defaults before saving again.
            restored=copy.deepcopy(saved['config'])
            restored['sourceSettings']['extraction']=extraction
            response=await client.put(saved_url,json={**body,'config':restored,'expected_revision':1})
            assert response.status_code==200,response.text
            assert response.json()['config']['input']==config['input']
            assert response.json()['config']['sourceSettings']['extraction'] is None
            assert (await client.request('DELETE',saved_url,json={'expected_revision':2})).status_code==200
        datasource=copy.deepcopy(config)
        datasource['sourceSettings'].update(kind='datasource',datasource_id='source-id',extraction=extraction)
        for invalid in (extraction,None):
            datasource['sourceSettings']['extraction']=invalid
            assert (await client.post('/studio/artifacts',json={**body,'config':datasource})).status_code==422
        datasource['sourceSettings']['extraction']={**extraction,'resource':'device_assets'}
        validated=studio.ArtifactInput.model_validate({**body,'config':datasource})
        assert validated.config['sourceSettings']['extraction']['resource']=='device_assets'
        principal['id']='other'
        assert not (await client.get('/studio/artifacts')).json()
        assert (await client.request('DELETE',url,json={'expected_revision':2})).status_code==404
        principal.update(id='owner',auth_type='api_key',external_user_id='someone')
        assert (await client.put(url,json={**body,'expected_revision':2})).status_code==404
        principal.update(auth_type='jwt',external_user_id='')
        assert (await client.request('DELETE',url,json={'expected_revision':1})).status_code==409
        assert (await client.request('DELETE',url,json={'expected_revision':2})).status_code==200
        # Old saved pipelines remain readable/writable without silently upgrading their rules.
        legacy={k:v for k,v in config.items() if k not in ('schemaVersion','sourceSettings')}
        legacy['nodes']=[{k:v for k,v in n.items() if k in ('id','kind','x','y','field','value','interval')} for n in config['nodes']]
        assert (await client.post('/studio/artifacts',json={**body,'config':legacy})).status_code==201
    await engine.dispose()
    print('PASS: v2 fields/source persistence, inactive extraction save/reopen, strict datasource validation, invalid graphs/input/dates, optimistic revisions, owner/end-user isolation, deletion and legacy compatibility')

asyncio.run(main())
