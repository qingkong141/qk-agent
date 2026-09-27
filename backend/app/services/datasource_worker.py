"""Bounded read-only driver operations; executed in a disposable child process."""
import base64
import json
import re
import sys
import time
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import quote

from app.services.datasource_schema import Connection, ReadInput


def identifier(value,mark='"'):
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*',value):
        raise ValueError('该数据源仅支持字母、数字、下划线组成的表/字段名，可用点分隔库或schema')
    return '.'.join(mark+p+mark for p in value.split('.'))


def sql_for(read,mark='"'):
    sql='SELECT * FROM '+identifier(read.resource,mark)
    params=[]
    if read.products:
        sql+=' WHERE '+identifier(read.product_field,mark)+' IN ('+','.join(['%s']*len(read.products))+')'
        params=list(read.products)
    return sql+f' LIMIT {read.limit+1}',params


def dbapi(conn,kind,operation,read):
    with closing(conn),closing(conn.cursor()) as cursor:
        if kind in ('mysql','polardb-mysql'):
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
            catalog='SHOW TABLES';mark='`'
        elif kind in ('postgres','polardb-postgres'):
            cursor.execute('SET default_transaction_read_only = on');cursor.execute("SET statement_timeout = '15000'")
            catalog="SELECT table_schema || '.' || table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1 LIMIT 500";mark='"'
        else: catalog='SHOW TABLES';mark='`'
        if operation in ('test','catalog'):
            cursor.execute('SELECT 1' if operation=='test' else catalog)
        else:
            query,params=sql_for(read,mark);cursor.execute(query,params or None)
        values=cursor.fetchmany(501 if operation=='catalog' else read.limit+1 if read else 1)
        columns=[str(c[0]) for c in cursor.description]
        if operation=='test': return {'connected':True}
        if operation=='catalog': return {'resources':[str(row[0]) for row in values[:500]],'truncated':len(values)>500}
        return {'rows':[dict(zip(columns,row)) for row in values],'selection':'服务端产品条件过滤'}


def execute(c,password,operation,read=None):
    kind=c.type
    if kind=='influxdb':
        import httpx
        query='SHOW MEASUREMENTS LIMIT '+('1' if operation=='test' else '501')
        params={}
        if read:
            # One measurement, optionally qualified by retention policy; no arbitrary InfluxQL.
            if len(read.resource.split('.'))>2:raise ValueError('InfluxDB请输入表名或保留策略.表名')
            query='SELECT * FROM '+identifier(read.resource)+f' WHERE time >= now() - {read.lookback_minutes}m'
            if read.products:
                field=identifier(read.product_field)
                params={f'product{i}':value for i,value in enumerate(read.products)}
                query+=' AND ('+' OR '.join(f'{field} = ${key}' for key in params)+')'
            query+=f' ORDER BY time DESC LIMIT {read.limit+1}'
        with httpx.Client(timeout=15,trust_env=False,follow_redirects=False,auth=(c.username,password) if c.username else None) as client:
            with client.stream('GET',f'{"https" if c.tls else "http"}://{c.host}:{c.port}/query',params={'db':c.database,'q':query,'params':json.dumps(params)}) as response:
                response.raise_for_status();raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>1000000:raise ValueError('本次响应超过1MB，请缩小读取范围')
                body=json.loads(raw)
        if body.get('error') or any(r.get('error') for r in body.get('results',[])):
            raise ValueError('InfluxDB查询失败，请检查数据库、保留策略、字段和读取权限')
        if not isinstance(body.get('results'),list) or not body['results']:
            raise ValueError('InfluxDB未返回有效查询结果')
        series=[s for result in body['results'] for s in result.get('series',[])]
        if operation=='test':return {'connected':True}
        if operation=='catalog':
            names=[str(row[0]) for s in series for row in s.get('values',[])]
            return {'resources':names[:500],'truncated':len(names)>500}
        rows=[{**s.get('tags',{}),**dict(zip(s['columns'],row))} for s in series for row in s.get('values',[])]
        return {'rows':rows,'selection':f'InfluxDB最近{read.lookback_minutes}分钟，服务端产品条件过滤，时间倒序；未指定保留策略时使用数据库默认策略'}
    if kind in ('mysql','polardb-mysql'):
        import pymysql
        return dbapi(pymysql.connect(host=c.host,port=c.port,user=c.username,password=password,database=c.database or None,
            connect_timeout=8,read_timeout=15,write_timeout=8,ssl_verify_cert=c.tls,ssl_verify_identity=c.tls,ssl={} if c.tls else None),kind,operation,read)
    if kind in ('postgres','polardb-postgres'):
        import psycopg
        return dbapi(psycopg.connect(host=c.host,port=c.port,user=c.username,password=password,dbname=c.database or 'postgres',
            connect_timeout=8,sslmode='verify-full' if c.tls else 'disable'),kind,operation,read)
    if kind=='hive':
        from pyhive import hive
        options=dict(host=c.host,port=c.port,username=c.username or None,database=c.database or 'default',auth=c.auth.upper())
        if c.auth=='ldap': options['password']=password
        return dbapi(hive.Connection(**options),kind,operation,read)
    if kind=='clickhouse':
        import clickhouse_connect
        with closing(clickhouse_connect.get_client(host=c.host,port=c.port,username=c.username or 'default',password=password,database=c.database or 'default',secure=c.tls,verify=True,connect_timeout=8,send_receive_timeout=15)) as client:
            if operation=='test': client.query('SELECT 1');return {'connected':True}
            if operation=='catalog':
                result=client.query('SHOW TABLES');return {'resources':[str(row[0]) for row in result.result_rows[:500]],'truncated':len(result.result_rows)>500}
            query,params=sql_for(read,'`')
            result=client.query(query,parameters=tuple(params) if params else None,settings={'readonly':1,'max_execution_time':15,'max_result_bytes':1000000,'result_overflow_mode':'throw'})
            return {'rows':[dict(zip(result.column_names,row)) for row in result.result_rows],'selection':'服务端产品条件过滤'}
    if kind=='iotdb':
        from iotdb.Session import Session
        session=Session(c.host,c.port,c.username or 'root',password,fetch_size=1000,use_ssl=c.tls,connection_timeout_in_ms=8000,enable_redirection=False)
        try:
            session.open(False)
            sql='SHOW VERSION' if operation=='test' else 'SHOW DEVICES LIMIT 501' if operation=='catalog' else sql_for(read,'`')[0]
            if read:
                # Tree-model identifiers use root paths, and literals are escaped without accepting raw SQL.
                if not re.fullmatch(r'root(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+',read.resource): raise ValueError('IoTDB请输入root开头的设备路径')
                sql=f'SELECT * FROM {read.resource} LIMIT {read.limit+1}'
            with session.execute_query_statement(sql,15000) as result:
                if operation=='test': return {'connected':True}
                frame=result.todf();rows=json.loads(frame.to_json(orient='records',date_format='iso'))
                if operation=='catalog': return {'resources':[str(next(iter(r.values()))) for r in rows[:500]],'truncated':len(rows)>500}
                return {'rows':rows,'selection':'本次读取窗口内按产品过滤'}
        finally: session.close()
    if kind in ('kafka','roma-mqs'):
        from kafka import KafkaConsumer,TopicPartition
        options=dict(bootstrap_servers=[f'{c.host}:{c.port}'],group_id=None,enable_auto_commit=False,
            request_timeout_ms=10000,api_version_auto_timeout_ms=8000,fetch_max_bytes=1000000,max_partition_fetch_bytes=600000,
            security_protocol=('SASL_SSL' if c.tls else 'SASL_PLAINTEXT') if c.auth!='none' else ('SSL' if c.tls else 'PLAINTEXT'))
        if c.auth!='none': options.update(sasl_mechanism=c.auth.upper(),sasl_plain_username=c.username,sasl_plain_password=password)
        with closing(KafkaConsumer(**options)) as consumer:
            if operation in ('test','catalog'):
                topics=sorted(consumer.topics());return {'connected':True} if operation=='test' else {'resources':topics[:500],'truncated':len(topics)>500}
            partitions=sorted(consumer.partitions_for_topic(read.resource) or [])
            if not partitions: raise ValueError('主题不存在或无读取权限')
            if len(partitions)>100: raise ValueError('主题超过100个分区，请使用较小的读取主题')
            assigned=[TopicPartition(read.resource,p) for p in partitions];consumer.assign(assigned)
            ends=consumer.end_offsets(assigned);starts=consumer.beginning_offsets(assigned)
            for p in assigned: consumer.seek(p,max(starts[p],ends[p]-read.limit-1))
            rows=[];deadline=time.monotonic()+5
            while len(rows)<=read.limit and time.monotonic()<deadline:
                batch=consumer.poll(timeout_ms=500,max_records=read.limit+1-len(rows))
                for records in batch.values():
                    for r in records:
                        text=(r.value or b'').decode('utf-8',errors='replace')
                        try: value=json.loads(text)
                        except ValueError: value=text
                        row=dict(value) if isinstance(value,dict) else {'value':value}
                        row['_kafka']={'topic':r.topic,'partition':r.partition,'offset':r.offset,'timestamp':r.timestamp};rows.append(row)
                if all(consumer.position(p)>=ends[p] for p in assigned): break
            return {'rows':rows,'selection':'各分区尾部读取窗口；不提交消费位点，窗口内按产品过滤'}
    if kind=='hbase':
        import httpx
        url=f'{"https" if c.tls else "http"}://{c.host}:{c.port}/'
        path='version/cluster' if operation=='test' else '' if operation=='catalog' else quote(read.resource,safe='')+'/*'
        with httpx.Client(timeout=15,follow_redirects=False,auth=(c.username,password) if c.username else None) as client:
            with client.stream('GET',url+path,params={'limit':read.limit+1,'maxversions':1} if read else None,headers={'Accept':'application/json'}) as response:
                response.raise_for_status();raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>1000000: raise ValueError('本次响应超过1MB，请缩小读取范围')
                content=json.loads(raw)
            if operation=='test': return {'connected':True}
            if operation=='catalog':
                tables=content.get('table',[]);return {'resources':[v['name'] for v in tables[:500]],'truncated':len(tables)>500}
            decode=lambda s:base64.b64decode(s).decode('utf-8',errors='replace')
            rows=[{'_rowkey':decode(row['key']),**{decode(cell['column']):decode(cell['$']) for cell in row.get('Cell',[])}} for row in content.get('Row',[])]
            return {'rows':rows,'selection':'前部读取窗口内按产品过滤；列值保持文本'}
    raise ValueError('不支持的数据源类型')


def normalize(result,read):
    if read:
        raw=result['rows'];truncated=len(raw)>read.limit;raw=raw[:read.limit]
        if read.products:
            if raw and any(read.product_field not in r for r in raw): raise ValueError('产品字段不存在，请核对返回字段名')
            raw=[r for r in raw if str(r.get(read.product_field)) in read.products]
        result.update(rows=raw,truncated=truncated,fields=list(dict.fromkeys(k for r in raw for k in r)))
    def encode(v):
        if isinstance(v,(date,datetime)):return v.isoformat()
        if isinstance(v,Decimal):return str(v)
        if isinstance(v,bytes):return {'base64':base64.b64encode(v).decode()}
        raise TypeError('数据包含不支持的类型')
    text=json.dumps(result,ensure_ascii=False,default=encode,allow_nan=False)
    if len(text.encode())>600000:raise ValueError('本次数据超过600KB，请降低读取条数')
    return text


if __name__=='__main__':
    try:
        payload=json.loads(sys.stdin.buffer.read().decode('utf-8'));c=Connection.model_validate(payload['connection']);read=ReadInput.model_validate(payload['read']) if payload.get('read') else None
        sys.stdout.buffer.write(normalize(execute(c,payload.get('password',''),payload['operation'],read),read).encode('utf-8'))
    except Exception as error:
        # Driver exception strings may include credentials or connection URLs: never forward them.
        message=str(error) if isinstance(error,ValueError) and type(error) is ValueError else '连接或读取失败，请检查服务地址、网络、只读账号权限、TLS及认证方式'
        sys.stdout.buffer.write(json.dumps({'error':message,'error_type':type(error).__name__},ensure_ascii=False).encode('utf-8'));sys.exit(1)
