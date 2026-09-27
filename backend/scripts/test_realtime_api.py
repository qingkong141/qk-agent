"""Isolated realtime lifecycle, window semantics, and polling tests."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import realtime as api
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.realtime import RealtimeTask
from app.services import realtime_poll as poll
from app.services.realtime_engine import calculate, merge_window


def node(kind, **extra):
    return dict(id=kind, kind=kind, name=kind, x=0, y=0, field='value', value='0', interval=1,
                compare='gte', valueType='number', keys=[], keep='first', parseMode='json', script='{}',
                conflict='error', outputField='result', trueValue='true', falseValue='false', **extra)


def config(*operators):
    nodes = [node('source'), *operators, node('output')]
    return api.Config(nodes=nodes, edges=[{'from': a['id'], 'to': b['id']} for a,b in zip(nodes,nodes[1:])]).model_dump(by_alias=True)


def point(minute, value, device='A'):
    return {'deviceId': device, 'time': f'2026-09-27T00:{minute:02}:00Z', 'value': value}


async def main():
    def changed(kind, **changes): return {**node(kind), **changes}
    cfg=config(changed('filter'), changed('aggregate'), changed('align'), changed('fill'), changed('constant',value='2'), changed('extreme'))
    inputs=[point(0,10),point(1,10),point(3,30),point(3,100,'B'),point(4,-1)]
    merged, _, _, _=merge_window([], inputs, 15)
    result, trace=calculate(cfg, merged)
    a=[r for r in result if r['deviceId']=='A']; b=[r for r in result if r['deviceId']=='B']
    assert [r['value'] for r in a]==[10,10,0,30] and a[1]['constant']
    assert a[2]['extreme']=='最小值' and a[3]['extreme']=='最大值'
    assert b[0]['value']==100 and b[0]['consecutive']==1
    assert len(trace)==8 and trace[1]['before']==5 and trace[1]['after']==4
    same, duplicates, _, _=merge_window(merged, inputs,15)
    assert duplicates==5 and same==merged
    corrected, _, _, _=merge_window(same,[point(1,20)],15)
    assert len(corrected)==5 and corrected[1]['value']==20
    late,_,expired,watermark=merge_window(corrected,[point(2,25),point(20,60)],15)
    assert len(late)==1 and expired==6 and watermark.startswith('2026-09-27T00:20')
    assert calculate(config(changed('fill')), [point(0,0),point(1,None)])[0][0]['value']==0
    avg=calculate(config(changed('aggregate',interval=5)),[point(0,None),point(1,10),point(2,20)])[0]
    assert avg[0]['value']==15 and avg[0]['count']==2
    assert poll.metric_value({'app':[{'identifier':'infusion','data':[{'type':'heartBeat','values':[{'key':'DevicesPower','value':'61'}]}]}]},'metric.infusion.heartBeat.DevicesPower')==61
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection: await connection.run_sync(RealtimeTask.__table__.create)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def db_override():
        async with sessions() as db: yield db
    async def user_override(): return principal
    app=FastAPI(); app.include_router(api.router)
    app.dependency_overrides[get_db]=db_override; app.dependency_overrides[get_current_user]=user_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        body={'name':'实时分析','config':cfg}; base='/studio/realtime'
        for invalid in [dict(cfg,edges=[]),dict(cfg,window_minutes=0),dict(cfg,nodes=[*cfg['nodes'],cfg['nodes'][1]])]:
            assert (await client.post(base,json={**body,'config':invalid})).status_code==422
        response=await client.post(base,json=body); assert response.status_code==201,response.text
        task=response.json(); path=f"{base}/{task['id']}"
        assert (await client.post(path+'/ingest',json={'rows':inputs})).status_code==409
        assert (await client.post(path+'/start',json={'expected_revision':9})).status_code==409
        assert (await client.post(path+'/start',json={'expected_revision':1})).json()['status']=='running'
        for bad in [point(0,True),point(0,'1'),dict(point(0,1),time='bad')]:
            assert (await client.post(path+'/ingest',json={'rows':[bad]})).status_code==422
        one=(await client.post(path+'/ingest',json={'rows':inputs[:1]})).json()
        two=(await client.post(path+'/ingest',json={'rows':inputs[1:]})).json()
        assert one['runtime']['batch_count']==1 and two['runtime']['batch_count']==2
        assert two['runtime']['rows']==result and 'input' not in two['runtime']
        assert (await client.post(path+'/ingest',json={'rows':inputs})).json()['runtime']['duplicates']==5
        assert (await client.put(path,json={**body,'expected_revision':1})).status_code==409
        assert (await client.request('DELETE',path,json={'expected_revision':1})).status_code==409
        principal['id']='other'
        assert (await client.get(base)).json()==[] and (await client.get(path)).status_code==404
        principal.update(id='owner',auth_type='api_key',external_user_id='external')
        assert (await client.get(path)).status_code==404
        principal.update(auth_type='jwt',external_user_id='')
        assert (await client.post(path+'/stop')).json()['status']=='stopped'
        assert (await client.post(path+'/ingest',json={'rows':inputs})).status_code==409
        assert (await client.post(path+'/start',json={'expected_revision':1})).json()['runtime']['batch_count']==3
        await client.post(path+'/stop')
        edited=await client.put(path,json={**body,'expected_revision':1})
        assert edited.json()['revision']==2 and edited.json()['runtime']=={}
        # A calculation overflow stops the task and preserves the previous successful result.
        huge=config(changed('aggregate'))
        await client.put(path,json={**body,'config':huge,'expected_revision':2})
        await client.post(path+'/start',json={'expected_revision':3})
        await client.post(path+'/ingest',json={'rows':[point(0,10)]})
        error=await client.post(path+'/ingest',json={'rows':[point(1,1e308),dict(point(1,1e308),time='2026-09-27T00:01:10Z')]})
        assert error.status_code==400,error.text
        state=(await client.get(path)).json()
        assert state['status']=='error' and state['runtime']['rows'][0]['value']==10
        # Polling runs independently, empty reads and failures are observable; restart stops platform jobs.
        platform_cfg={**cfg,'source':{**cfg['source'],'kind':'platform'}}
        platform=(await client.post(base,json={**body,'config':platform_cfg})).json()
        async def empty_fetch(source,token): return []
        async def failing_fetch(source,token): raise ValueError('平台登录凭据已失效')
        with patch.object(poll,'async_session',sessions),patch.object(poll.settings,'PLATFORM_DEVICE_BASE_URL','http://test'):
            with patch.object(poll,'fetch',empty_fetch):
                assert (await client.post(f"{base}/{platform['id']}/start",json={'expected_revision':1},headers={'X-Platform-Token':'test'})).status_code==200
                await asyncio.sleep(.05)
                waiting=(await client.get(f"{base}/{platform['id']}")).json()
                assert waiting['runtime']['checked_at'] and waiting['runtime']['source_message']
                await poll.shutdown(); await poll.startup()
                assert (await client.get(f"{base}/{platform['id']}")).json()['status']=='stopped'
            with patch.object(poll,'fetch',failing_fetch):
                await client.post(f"{base}/{platform['id']}/start",json={'expected_revision':1},headers={'X-Platform-Token':'test'})
                await asyncio.sleep(.05)
                assert (await client.get(f"{base}/{platform['id']}")).json()['status']=='error'
            await poll.shutdown()
        assert (await client.request('DELETE',path,json={'expected_revision':3})).status_code==200
    await engine.dispose()
    print('PASS: realtime operators, windows, lifecycle, ownership, polling and restart recovery')

asyncio.run(main())
