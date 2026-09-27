"""Master identity immutability, alias uniqueness, rollback, export and isolation."""
import asyncio
import io
import os
import sys
import zipfile
from xml.etree import ElementTree
from pathlib import Path
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import master_index as api
from app.db.session import Base,get_db
from app.dependencies import get_current_user


async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def user():return principal
    async def database():
        async with sessions() as db:yield db
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_current_user]=user;app.dependency_overrides[get_db]=database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        base='/studio/master-index';data={'name':'设备一','kind':'device','aliases':[{'system':'设备平台','external_id':'DEV-01','attributes':{'电量':0}},{'system':'资产','external_id':'ASSET-01','attributes':{'位置':'输液室'}}]}
        r=await client.post(base,json=data);assert r.status_code==201,r.text
        item=r.json();path=base+'/'+item['id'];assert item['code'].startswith('DEV-')
        assert (await client.get(base,params={'q':'ASSET-01'})).json()['items'][0]['code']==item['code']
        assert next(a for a in (await client.get(path)).json()['aliases'] if a['system']=='设备平台')['attributes']['电量']==0
        assert (await client.post(base,json=data)).status_code==409
        assert len((await client.get(base)).json()['items'])==1
        data['aliases'][0]['external_id']='DEV-02';data['aliases'][1]['external_id']='ASSET-02'
        second=(await client.post(base,json=data)).json()
        collision={**data,'expected_revision':1,'name':'不应修改'}
        assert (await client.put(path,json=collision)).status_code==409
        after=(await client.get(path)).json();assert after['name']=='设备一' and after['revision']==1 and after['code']==item['code']
        original={'name':'设备一更名','kind':'device','aliases':item['aliases'],'expected_revision':1}
        updated=(await client.put(path,json=original)).json();assert updated['revision']==2 and updated['code']==item['code']
        assert (await client.put(path,json=original)).status_code==409
        assert (await client.put(path,json={**original,'kind':'patient','expected_revision':2})).status_code==400
        assert (await client.request('DELETE',path,json={'expected_revision':1})).status_code==409
        exported=await client.get(base+'/export');assert 'ASSET-01' in exported.text and '电量' not in exported.text
        rules=await client.get(base+'/rules')
        assert rules.status_code==200
        assert rules.headers['content-type']=='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        assert 'master-index-rules.docx' in rules.headers['content-disposition']
        with zipfile.ZipFile(io.BytesIO(rules.content)) as document:
            assert document.testzip() is None
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            xml=ElementTree.fromstring(document.read('word/document.xml'))
            paragraphs=[''.join(p.itertext()) for p in xml.findall('.//w:p',ns)]
            for line in api.RULES.splitlines():
                if line.strip() and not line.startswith('#'): assert line in paragraphs
            assert len([p for p in paragraphs if p[:1].isdigit()])==8
            assert xml.find('.//w:pStyle',ns).get('{'+ns['w']+'}val')=='Title'
            ElementTree.fromstring(document.read('word/styles.xml'))
        patient={**original,'kind':'patient','name':'就诊对象001'}
        assert (await client.post(base,json=patient)).json()['code'].startswith('PAT-')
        principal['id']='other'
        assert not (await client.get(base)).json()['items']
        assert (await client.get(path)).status_code==404
        assert 'ASSET-01' not in (await client.get(base+'/export')).text
        assert (await client.post(base,json=data)).status_code==201
        principal['id']='owner'
        assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==200
        assert not (await client.get(base,params={'q':'ASSET-01','kind':'device'})).json()['items']
        assert (await client.post(base,json=original)).status_code==201
        assert (await client.get(base,params={'q':'%'})).json()['items']==[]
    await engine.dispose();print('PASS: identity, duplicate rollback, immutable code, revisions, cross-system lookup, export and isolation')


asyncio.run(main())
