"""Validated DAG execution with typed references to input and preceding tool results."""
import asyncio
import json
import re
from time import monotonic

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from app.services import mcp_registry as registry


class Node(BaseModel):
    id: str = Field(pattern=r'^n[A-Za-z0-9_-]{1,50}$')
    name: str = Field(min_length=1,max_length=120)
    service_id: str = Field(min_length=1,max_length=100)
    tool: str = Field(min_length=1,max_length=200)
    arguments: dict = Field(default_factory=dict)
    x: float = Field(default=40,ge=0,le=20000,allow_inf_nan=False)
    y: float = Field(default=40,ge=0,le=20000,allow_inf_nan=False)


class Edge(BaseModel):
    source: str = Field(alias='from')
    to: str


class Config(BaseModel):
    nodes: list[Node] = Field(min_length=1,max_length=30)
    edges: list[Edge] = Field(default_factory=list,max_length=100)
    input_schema: dict = Field(default_factory=lambda:{'type':'object','properties':{}})
    example_input: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=180,ge=10,le=600)

    @model_validator(mode='after')
    def valid(self):
        order(self)
        registry.check_schema(self.input_schema)
        if len(json.dumps(self.model_dump()).encode())>1024*1024: raise ValueError('流程配置超过1MB')
        return self


def references(value):
    if isinstance(value,dict):
        if '$from' in value:
            if set(value)-{'$from','path'} or not isinstance(value['$from'],str) or not isinstance(value.get('path',''),str):
                raise ValueError('参数引用应使用 {"$from":"input或节点编号","path":"/字段路径"}')
            path=value.get('path','')
            if path and (not path.startswith('/') or re.search(r'~(?![01])',path)): raise ValueError('参数引用路径需为JSON Pointer，例如 /documents/0/title')
            yield value
        else:
            for child in value.values(): yield from references(child)
    elif isinstance(value,list):
        for child in value: yield from references(child)


def order(config):
    nodes={n.id:n for n in config.nodes}
    if len(nodes)!=len(config.nodes): raise ValueError('节点编号不能重复')
    parents={key:set() for key in nodes};children={key:set() for key in nodes}
    for edge in config.edges:
        if edge.source not in nodes or edge.to not in nodes or edge.source==edge.to: raise ValueError('连线端点无效')
        if edge.to in children[edge.source]: raise ValueError('连线不能重复')
        children[edge.source].add(edge.to);parents[edge.to].add(edge.source)
    queue=[key for key in nodes if not parents[key]];result=[];ancestors={key:set() for key in nodes}
    pending={key:set(value) for key,value in parents.items()}
    while queue:
        key=queue.pop(0);result.append(nodes[key])
        for target in sorted(children[key]):
            ancestors[target].update(ancestors[key]|{key});pending[target].remove(key)
            if not pending[target]: queue.append(target)
    if len(result)!=len(nodes): raise ValueError('流程存在循环连线，请调整为单向流程')
    if len(nodes)>1:
        visited=set();todo=[next(iter(nodes))]
        while todo:
            key=todo.pop()
            if key in visited: continue
            visited.add(key);todo.extend((parents[key]|children[key])-visited)
        if len(visited)!=len(nodes): raise ValueError('存在未连接的工具节点，请连接后执行')
    for node in result:
        for ref in references(node.arguments):
            if ref['$from']!='input' and ref['$from'] not in ancestors[node.id]:
                raise ValueError(f'“{node.name}”只能引用流程输入或已连线的前序节点')
    return result


def resolve(value, data, results):
    if isinstance(value,list): return [resolve(v,data,results) for v in value]
    if not isinstance(value,dict): return value
    if '$from' not in value: return {key:resolve(child,data,results) for key,child in value.items()}
    current=data if value['$from']=='input' else results[value['$from']]
    for segment in value.get('path','').split('/')[1:]:
        key=segment.replace('~1','/').replace('~0','~')
        try:
            if isinstance(current,list):
                if not re.fullmatch(r'0|[1-9][0-9]*',key): raise KeyError(key)
                current=current[int(key)]
            else: current=current[key]
        except (KeyError,IndexError,TypeError,ValueError) as exc: raise HTTPException(400,f'参数引用缺失：{value["$from"]}{value.get("path","")}') from exc
    return current


async def preflight(config,headers,db,user):
    await registry.validate_services({node.service_id for node in config.nodes},db,user)
    catalogs={}
    for node in config.nodes:
        if node.service_id not in catalogs: catalogs[node.service_id]=await registry.discover(node.service_id,headers,db,user)
        tool=next((t for t in catalogs[node.service_id] if t['name']==node.tool),None)
        if not tool: raise HTTPException(400,f'“{node.name}”选择的工具不存在')
        if not list(references(node.arguments)): registry.check_arguments(tool['inputSchema'],node.arguments)
    return catalogs


async def execute(config, data, headers, db, user):
    registry.check_arguments(config.input_schema,data)
    nodes=order(config);trace=[];results={};started=monotonic()
    async def run():
        catalogs=await preflight(config,headers,db,user)
        # Validate all input-only references before executing any tool with side effects.
        for node in nodes:
            for ref in references(node.arguments):
                if ref['$from']=='input': resolve(ref,data,{})
        for node in nodes:
            step={'id':node.id,'name':node.name,'service_id':node.service_id,'tool':node.tool,'status':'running'}
            trace.append(step);begin=monotonic()
            try:
                arguments=resolve(node.arguments,data,results)
                tool=next(t for t in catalogs[node.service_id] if t['name']==node.tool)
                registry.check_arguments(tool['inputSchema'],arguments)
                value=await registry.call(node.service_id,node.tool,arguments,headers,db,user)
                step.update(arguments=arguments,result=value['data'],status='error' if value['error'] else 'completed')
                if value['error']: raise HTTPException(400,f'“{node.name}”执行失败，后续节点未执行')
                results[node.id]=value['data']
            except HTTPException as exc:
                step.update(status='error',reason=str(exc.detail));raise
            finally: step['duration_ms']=round((monotonic()-begin)*1000)
        # Multiple terminal nodes remain separate named outputs.
        outgoing={edge.source for edge in config.edges}
        return {'status':'completed','outputs':{node.id:results[node.id] for node in nodes if node.id not in outgoing},'trace':trace}
    try: answer=await asyncio.wait_for(run(),timeout=config.timeout_seconds)
    except TimeoutError:
        if trace and trace[-1]['status']=='running': trace[-1]['status']='timeout'
        answer={'status':'timeout','reason':'执行超时，已保留完成的结果，后续节点未执行','outputs':results,'trace':trace}
    except HTTPException as exc: answer={'status':'error','reason':str(exc.detail),'outputs':results,'trace':trace}
    return {**answer,'duration_ms':round((monotonic()-started)*1000)}
