"""Exercise real device writes/analysis with deterministic model proposals in an isolated database."""
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import device_assistant as api
from app.services import device_assistant as service
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.studio import StudioArtifact


async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn: await conn.run_sync(StudioArtifact.__table__.create)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def database():
        async with sessions() as db: yield db
    async def user(): return principal
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[get_db]=database;app.dependency_overrides[get_current_user]=user
    plan=service.Plan(action='create_product',explanation='创建输液产品',product={'name':'输液监测','fields':[{'key':'battery','name':'剩余电量','type':'number','unit':'%','required':True,'minimum':0,'maximum':100}]})
    async def generate(*args): return plan
    base='/studio/device-assistant'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client, patch_async(generate):
        async def ask(thread=None,context=None):
            r=await client.post(base+'/ask',json={'question':'测试操作','thread_id':thread['id'] if thread else None,'expected_revision':thread['revision'] if thread else None,'context':context or {}})
            assert r.status_code==200,r.text
            return r.json()
        async def run(t):
            r=await client.post(f"{base}/threads/{t['id']}/turns/{t['turns'][-1]['id']}/execute",json={'expected_revision':t['revision']});assert r.status_code==200,r.text
            return r.json()
        t=await ask();assert not (await client.get(base+'/catalog')).json()['products']
        t=await run(t);product=t['turns'][-1]['execution']['id']
        assert (await client.post(f"{base}/threads/{t['id']}/turns/{t['turns'][-1]['id']}/execute",json={'expected_revision':t['revision']})).status_code==409
        plan=service.Plan(action='create_device',explanation='设备方案',product_id=product,device_code='INF-001',device_name='输液监测一号')
        t=await run(await ask(t));device=t['turns'][-1]['execution']['id']
        plan=service.Plan(action='report',explanation='上报',device_id=device,metric='battery',values=[61,50,35,0])
        t=await run(await ask(t))
        plan=service.Plan(action='analyze',explanation='分析',device_id=device,metric='battery',lower=40)
        t=await ask(t);result=t['turns'][-1]['result']
        assert result['count']==4 and result['abnormal']==2 and result['delta']==-61 and result['minimum']==0,result
        assert result['mean']==36.5
        for values in [{'battery':101},{'battery':True},{'battery':'10'},{'unknown':10},{}]:
            r=await client.post(f'{base}/devices/{device}/reports',json={'rows':[{'time':datetime.now(timezone.utc).isoformat(),'values':values}]});assert r.status_code==422,r.text
        assert len((await client.get(base+'/catalog')).json()['devices'])==1
        plan=service.Plan(action='create_device',explanation='重复设备',product_id=product,device_code='INF-001',device_name='重复')
        duplicate=await ask()
        assert (await client.post(f"{base}/threads/{duplicate['id']}/turns/{duplicate['turns'][-1]['id']}/execute",json={'expected_revision':duplicate['revision']})).status_code==409
        principal['id']='other'
        assert (await client.get(base+'/catalog')).json()=={'products':[],'devices':[]}
        assert (await client.get(base+'/threads/'+t['id'])).status_code==404
        assert (await client.post(f'{base}/devices/{device}/reports',json={'rows':[{'time':datetime.now(timezone.utc).isoformat(),'values':{'battery':20}}]})).status_code==404
        principal.update(id='owner',auth_type='api_key',external_user_id='tenant2')
        assert not (await client.get(base+'/catalog')).json()['devices']
        principal.update(auth_type='jwt',external_user_id='')
        plan=service.Plan(action='answer',explanation='按照知识操作',references=['mapping'])
        q=await ask();assert q['turns'][0]['plan']['references']==['mapping']
        assert (await client.post(base+'/ask',json={'question':' '})).status_code==400
        assert service.analyze([])['count']==0
        assert service.analyze([{'time':'2026-09-27','value':None}])['missing']==1
        print('PASS: product/device creation, execute-once, persistent report validation, measured trend/threshold, knowledge references and ownership')
    await engine.dispose()


class patch_async:
    def __init__(self,generate): self.patch=patch.object(service,'generate',generate)
    async def __aenter__(self): self.patch.start()
    async def __aexit__(self,*args): self.patch.stop()


if __name__=='__main__': asyncio.run(main())
