"""Driver contracts and actual isolated HTTP extraction, never asserts remote DB availability."""
import asyncio
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
os.environ['SECRET_KEY']='datasource-isolated-test-key'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import datasources as api
from app.db.session import Base,get_db
from app.dependencies import get_current_user
from app.models.datasource import DataSource
from app.services.datasource_schema import ReadInput,Connection,TYPES
from app.services.datasource_worker import sql_for,normalize


class Server(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        encode=lambda value:base64.b64encode(value.encode()).decode()
        if self.path.startswith('/version'):data={'Version':'isolated-contract-server'}
        elif self.path=='/':data={'table':[{'name':'device_data'}]}
        else:
            assert 'limit=3' in self.path
            data={'Row':[{'key':encode(str(i)),'Cell':[{'column':encode('productKey'),'$':encode(product)},{'column':encode('battery'),'$':encode(str(value))}]} for i,product,value in [(1,'P1',0),(2,'P2',61),(3,'P1',35)]]}
        raw=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


async def main():
    # Import every installed native/protocol driver.
    import pymysql,psycopg,kafka,clickhouse_connect
    from pyhive import hive
    from iotdb.Session import Session
    assert len(TYPES)==10
    query,params=sql_for(ReadInput(resource='public.metrics',products=["a' OR 1=1 --"],limit=10))
    assert "a'" not in query and params==["a' OR 1=1 --"] and 'LIMIT 11' in query
    try:sql_for(ReadInput(resource='a:b'))
    except ValueError:pass
    else:raise AssertionError('unsafe identifier')
    result=json.loads(normalize({'rows':[{'productKey':'P1','v':0},{'productKey':'P2','v':None},{'productKey':'P1','v':2}]},ReadInput(resource='data',limit=2,products=['P1'])))
    assert result['rows']==[{'productKey':'P1','v':0}] and result['truncated']
    server=ThreadingHTTPServer(('127.0.0.1',0),Server);threading.Thread(target=server.serve_forever,daemon=True).start()
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False);principal={'id':'owner','auth_type':'jwt','external_user_id':''}
    async def user():return principal
    async def database():
        async with sessions() as db:yield db
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_current_user]=user;app.dependency_overrides[get_db]=database
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            base='/studio/datasources';data={'name':'隔离HBase协议测试','connection':{'type':'hbase','host':'127.0.0.1','port':server.server_port},'password':'private-test-secret'}
            r=await client.post(base,json=data);assert r.status_code==201,r.text;item=r.json();path=base+'/'+item['id'];assert item['has_password'] and 'private-test-secret' not in r.text
            async with sessions() as db:
                saved=await db.scalar(select(DataSource));assert saved.credential!='private-test-secret' and api.cipher().decrypt(saved.credential.encode()).decode()=='private-test-secret'
            r=await client.post(path+'/test');assert r.json()['ok'],r.text
            r=await client.get(path+'/resources');assert r.json()['resources']==['device_data'],r.text
            r=await client.post(path+'/extract',json={'resource':'device_data','limit':2,'products':['P1']});assert r.status_code==200,r.text
            assert r.json()['rows'][0]['battery']=='0' and len(r.json()['rows'])==1 and r.json()['truncated']
            r=await client.post(path+'/extract',json={'resource':'device_data','limit':2,'products':['P1','P2']});assert len(r.json()['rows'])==2
            r=await client.put(path,json={**data,'password':None,'expected_revision':1});assert r.json()['has_password'] and not r.json()['last_test']
            assert (await client.put(path,json={**data,'expected_revision':1})).status_code==409
            principal['id']='other';assert (await client.get(path)).status_code==404;assert (await client.get(base)).json()==[];principal['id']='owner'
            failed=await client.post(base,json={'name':'未启动服务','connection':{'type':'mysql','host':'127.0.0.1','port':1}})
            test=(await client.post(base+'/'+failed.json()['id']+'/test')).json();assert test['ok'] is False
            assert (await client.request('DELETE',path,json={'expected_revision':2})).status_code==200
    finally:server.shutdown();server.server_close();await engine.dispose()
    print('PASS: ten driver imports, parameterized reads, encrypted credentials, scoped CRUD, live HTTP worker, multi-product filter, failure state')


asyncio.run(main())
