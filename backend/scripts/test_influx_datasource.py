"""Exercise the actual datasource subprocess against a bounded Influx HTTP contract."""
import asyncio,json,os,sys,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit
os.environ['SECRET_KEY']='influx-isolated-test-key'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from app.api.datasources import run,secret
from app.services.datasource_schema import Connection,ReadInput

class Server(BaseHTTPRequestHandler):
    calls=[]
    def log_message(self,*args):pass
    def do_GET(self):
        query=parse_qs(urlsplit(self.path).query);q=query['q'][0];self.calls.append(query)
        assert query['db']==['metrics'] and self.headers.get('Authorization','').startswith('Basic ')
        if 'bad' in q:body={'results':[{'error':'upstream diagnostic must not escape'}]}
        elif 'large' in q:body={'padding':'x'*1000100,'results':[{}]}
        elif 'empty' in q:body={'results':[{}]}
        elif q.startswith('SHOW'):body={'results':[{'series':[{'columns':['name'],'values':[['m_gatewayflow']]}]}]}
        else:
            assert q.startswith('SELECT * FROM "rp_90days"."m_gatewayflow"')
            assert q.endswith('ORDER BY time DESC LIMIT 3')
            bindings=json.loads(query['params'][0]);assert bindings=={'product0':"P' OR true",'product1':'P2'}
            assert "P'" not in q and '"productKey" = $product0' in q
            body={'results':[{'series':[{'columns':['time','productKey','value'],'values':[['2026-09-27T00:00:00Z',"P' OR true",0],['2026-09-27T00:00:01Z','P2',None],['2026-09-27T00:00:02Z','P2',61]]}]}]}
        raw=json.dumps(body).encode();self.send_response(200);self.end_headers();self.wfile.write(raw)

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Server);threading.Thread(target=server.serve_forever,daemon=True).start()
    item=SimpleNamespace(connection=Connection(type='influxdb',host='127.0.0.1',port=server.server_port,database='metrics',username='reader').model_dump(),credential=secret('test-password'),name='Influx',revision=1)
    try:
        assert (await run(item,'test'))['connected']
        assert (await run(item,'catalog'))['resources']==['m_gatewayflow']
        result=await run(item,'read',ReadInput(resource='rp_90days.m_gatewayflow',limit=2,products=["P' OR true",'P2']))
        assert result['rows'][0]['value']==0 and result['rows'][1]['value'] is None and result['truncated']
        assert not (await run(item,'read',ReadInput(resource='empty')))['rows']
        for resource in ['bad','large','db.rp.table']:
            try:await run(item,'read',ReadInput(resource=resource))
            except HTTPException as e:assert e.status_code==502 and 'upstream diagnostic' not in e.detail and 'test-password' not in e.detail
            else:raise AssertionError('expected rejection')
    finally:server.shutdown();server.server_close()
    print('PASS: real subprocess, authenticated Influx query, bound products, retention policy, 0/null, empty/error/oversize response')

asyncio.run(main())
