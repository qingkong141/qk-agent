"""Protocol debug persistence/ownership/version tests using an isolated in-memory database."""
import asyncio
import os
import sys
from pathlib import Path
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import studio, protocol_debug as debug
from app.models.studio import StudioArtifact
from app.db.session import get_db
from app.dependencies import get_current_user


async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(StudioArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal = {'id':'owner','auth_type':'jwt','external_user_id':''}
    async def db_override():
        async with sessions() as db:
            yield db
    async def user_override(): return principal
    app=FastAPI();app.include_router(studio.router);app.include_router(debug.router)
    app.dependency_overrides[get_db]=db_override;app.dependency_overrides[get_current_user]=user_override
    model={'name':'model','fields':[{'key':'battery','name':'电量','type':'number','unit':'%','required':True,'minimum':0,'maximum':100}]}
    semantic={'schemaVersion':2,'name':'语义','sourceModel':model,'targetModel':model,'mappings':[],'mode':'script','script':'{"battery":battery}','raw':'{"battery":61}','sampleSource':'sample'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        m=(await client.post('/studio/artifacts',json={'name':'语义','kind':'mapping','config':semantic})).json()
        syntax={'name':'语法','inputFormat':'json','raw':'{"power":61}','parameters':{},'script':'{"battery":payload.power}','semanticId':m['id'],'semanticRevision':1}
        s=(await client.post('/studio/artifacts',json={'name':'语法','kind':'syntax','config':syntax})).json()
        settings={'name':'调试','syntaxId':s['id'],'syntaxRevision':1,'semanticId':m['id'],'semanticRevision':1,'inputFormat':'json','raw':'{"power":61}','parametersText':'{}','productName':'输液设备','productKey':'p','deviceId':'d','scenario':'normal'}
        response=await client.post('/studio/debug/runs',json={**settings,'syntax':{'script':'tampered'}})
        assert response.status_code==201,response.text
        run=response.json();url='/studio/debug/runs/'+run['id']
        assert run['config']['settings']['syntax']['script']==syntax['script']
        assert not any(x['kind']=='protocol_run' for x in (await client.get('/studio/artifacts')).json())
        assert (await client.post('/studio/debug/configs',json={'runId':run['id']})).status_code==400
        stages=[{'key':key,'name':key,'status':'passed','message':'ok'} for key in debug.STAGES]
        report={'stages':stages,'parsed':{'battery':61},'output':{'battery':61},'checks':[{'key':'battery','ok':True}],'durationMs':12}
        assert (await client.put(url,json={**report,'output':{'battery':125}})).status_code==400
        assert (await client.put(url,json={**report,'output':{'battery':False}})).status_code==400
        assert (await client.put(url,json={**report,'stages':stages[:-1]})).status_code==422
        assert (await client.put(url,json=report)).status_code==200
        assert (await client.put(url,json=report)).status_code==409
        saved=await client.post('/studio/debug/configs',json={'runId':run['id']});assert saved.status_code==201,saved.text
        cfg=saved.json();cfg_url='/studio/debug/configs/'+cfg['id']
        # No failed run can become a saved config, including a client bypass of the UI button.
        failed=(await client.post('/studio/debug/runs',json={**settings,'parametersText':'[]'})).json()
        bad_stages=[{**row,'status':'failed' if i==1 else 'passed' if i==0 else 'skipped'} for i,row in enumerate(stages)]
        assert (await client.put('/studio/debug/runs/'+failed['id'],json={**report,'stages':bad_stages})).status_code==200
        assert (await client.post('/studio/debug/configs',json={'runId':failed['id']})).status_code==400
        # Saved snapshots survive converter revisions and deletion; new stale bindings are refused.
        assert (await client.put('/studio/artifacts/'+s['id'],json={'name':'语法2','kind':'syntax','config':{**syntax,'script':'{"battery":0}'},'expected_revision':1})).status_code==200
        assert (await client.post('/studio/debug/runs',json=settings)).status_code==409
        old=(await client.post('/studio/debug/runs',json={**settings,'configurationId':cfg['id']})).json()
        assert old['config']['settings']['syntax']['script']==syntax['script']
        assert (await client.put('/studio/debug/runs/'+old['id'],json=report)).status_code==200
        assert (await client.put(cfg_url,json={'runId':old['id'],'expected_revision':1})).status_code==200
        assert (await client.put(cfg_url,json={'runId':old['id'],'expected_revision':1})).status_code==409
        principal['id']='other'
        assert (await client.get(url)).status_code==404
        assert (await client.get('/studio/debug/runs')).json()==[]
        assert (await client.post('/studio/debug/configs',json={'runId':run['id']})).status_code==404
        assert (await client.post('/studio/debug/runs',json=settings)).status_code==404
        assert (await client.request('DELETE',cfg_url,json={'expected_revision':2})).status_code==404
        principal.update(id='owner',auth_type='api_key',external_user_id='person-2')
        assert (await client.get(url)).status_code==404
        principal['external_user_id']=''
        assert (await client.get('/studio/debug/runs')).status_code==400
        principal['auth_type']='jwt'
        assert (await client.request('DELETE',cfg_url,json={'expected_revision':1})).status_code==409
        assert (await client.request('DELETE',cfg_url,json={'expected_revision':2})).status_code==200
        assert (await client.get(url)).status_code==200
    await engine.dispose()
    print('PASS: pinned sessions, immutable reports, failed/range-invalid save rejection, revisions, history and owner isolation')

asyncio.run(main())
