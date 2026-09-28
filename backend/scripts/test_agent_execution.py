"""Deterministic conversation, budgets, partial results and stream cancellation checks."""
import asyncio
import copy
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from pydantic import ValidationError
from app.api import agent_studio as api


async def models():return [{'id':'test-model'}]
def config(**options):return api.Config(model='test-model',prompt='query',services=['products'],**options)
class Session:
    async def list_tools(self):return SimpleNamespace(tools=[SimpleNamespace(name='lookup',description='read values',inputSchema={'type':'object'})])
    async def call_tool(self,name,args):return SimpleNamespace(structuredContent={'rows':[{'deviceId':'INF-031','value':args.get('n',1)}]},isError=False)
@asynccontextmanager
async def connect(*args):yield Session()


class Model:
    def __init__(self,total=7,batch=False,repeated=False,slow=False):
        self.total=total;self.batch=batch;self.repeated=repeated;self.slow=slow;self.count=0;self.seen=[];self.cancelled=asyncio.Event();self.waiting=asyncio.Event()
    def bind_tools(self,*args):return self
    async def astream(self,messages):
        result=await self.ainvoke(messages)
        if result.tool_calls:
            for index,call in enumerate(result.tool_calls):
                args=json.dumps(call['args'])
                yield AIMessageChunk(content='',tool_call_chunks=[{'name':call['name'],'args':args[:3],'id':call['id'],'index':index}])
                yield AIMessageChunk(content='',tool_call_chunks=[{'name':None,'args':args[3:],'id':None,'index':index}])
        else:
            for char in result.content:yield AIMessageChunk(content=char)
    async def ainvoke(self,messages):
        self.seen.append(list(messages));self.count+=1
        if self.slow and self.count>1:
            self.waiting.set()
            try:await asyncio.Event().wait()
            finally:self.cancelled.set()
        if self.count>self.total:return AIMessage(content='完成')
        indices=range(self.total) if self.batch else [0 if self.repeated else self.count]
        return AIMessage(content='',tool_calls=[{'name':'lookup','args':{'n':n},'id':f'call-{self.count}-{n}','type':'tool_call'} for n in indices])


async def main():
    for fields in [dict(max_tool_calls=0),dict(max_tool_calls=101),dict(timeout_seconds=9),dict(timeout_seconds=601)]:
        try:config(**fields);raise AssertionError('invalid limit accepted')
        except ValidationError:pass
    assert config().max_tool_calls==20 and config().timeout_seconds==180
    with patch.object(api,'available_models',models),patch.object(api.studio_mcp,'connect',connect):
        async def execute(model,settings=None,history=None,progress=None):
            with patch.object(api,'create_chat_model',lambda *args,**kwargs:model):
                return await api.safe_run(settings or config(),'继续查询',{},None,{},history,progress)
        model=Model();history=[api.HistoryTurn(question='设备编号为INF-031',answer='已收到编号')]
        seen=[]
        async def progress(value):seen.append(copy.deepcopy(value))
        result=await execute(model,history=history,progress=progress)
        assert result['status']=='completed' and len(result['trace'])==7 and model.count==8
        assert [m.content for m in model.seen[0][1:]]==['设备编号为INF-031','已收到编号','继续查询']
        assert set(len(r['trace']) for r in seen)==set(range(8))
        assert [r['answer'] for r in seen if r['answer']]==['完','完成']
        assert any('lookup' in r.get('message','') for r in seen)
        # A new run must receive the original request and stopped partial text, not only "continue".
        resumed=Model(total=0)
        await execute(resumed,history=[api.HistoryTurn(question='分析INF-031近一小时电量',answer='本轮未完成：已停止回答\n已查到电量，接下来分析趋势')])
        assert '中途停止的片段' in resumed.seen[0][0].content
        assert resumed.seen[0][1].content=='分析INF-031近一小时电量'
        assert '接下来分析趋势' in resumed.seen[0][2].content
        # A final answer after exactly the allowed calls is still permitted.
        result=await execute(Model(total=2),config(max_tool_calls=2));assert result['status']=='completed'
        result=await execute(Model(total=4,batch=True),config(max_tool_calls=2))
        assert result['status']=='tool_limit' and len(result['trace'])==2 and len(result['charts'])==2
        result=await execute(Model(total=6,repeated=True));assert result['status']=='repeated_tool' and len(result['trace'])==3
        slow=Model(slow=True)
        result=await execute(slow,config().model_copy(update={'timeout_seconds':0.03}))
        assert result['status']=='timeout' and len(result['trace'])==1 and slow.cancelled.is_set()
        # Closing the streaming iterator must cancel the in-flight model, not merely hide it.
        slow=Model(slow=True)
        with patch.object(api,'create_chat_model',lambda *args,**kwargs:slow):
            response=await api.stream_run(config(),api.Debug(config=config(),question='查询设备'),None,{},SimpleNamespace(headers={}))
            stream=response.body_iterator
            assert json.loads(await anext(stream))['status']=='running'
            while len(json.loads(await anext(stream))['trace'])<1:pass
            await asyncio.wait_for(slow.waiting.wait(),1)
            await stream.aclose()
            assert slow.cancelled.is_set()
        # Exercise Starlette's HTTP disconnect handling as well as directly closing the iterator.
        slow=Model(slow=True)
        with patch.object(api,'create_chat_model',lambda *args,**kwargs:slow):
            response=await api.stream_run(config(),api.Debug(config=config(),question='查询设备'),None,{},SimpleNamespace(headers={}))
            sent=[]
            async def send(message):sent.append(message)
            async def receive():
                await slow.waiting.wait()
                return {'type':'http.disconnect'}
            await asyncio.wait_for(response({'type':'http','asgi':{'spec_version':'2.0'}},receive,send),1)
            assert slow.cancelled.is_set() and any(m['type']=='http.response.body' for m in sent)
        # Cancellation must also propagate through a running MCP tool call.
        tool_waiting=asyncio.Event();tool_cancelled=asyncio.Event()
        class SlowSession(Session):
            async def call_tool(self,*args):
                tool_waiting.set()
                try:await asyncio.Event().wait()
                finally:tool_cancelled.set()
        @asynccontextmanager
        async def slow_connect(*args):yield SlowSession()
        with patch.object(api.studio_mcp,'connect',slow_connect),patch.object(api,'create_chat_model',lambda *args,**kwargs:Model()):
            response=await api.stream_run(config(),api.Debug(config=config(),question='查询设备'),None,{},SimpleNamespace(headers={}))
            stream=response.body_iterator;await anext(stream)
            await asyncio.wait_for(tool_waiting.wait(),1);await stream.aclose()
            assert tool_cancelled.is_set()
        # Successful stream includes ordered snapshots and an explicit terminal record.
        with patch.object(api,'create_chat_model',lambda *args,**kwargs:Model(total=1)):
            response=await api.stream_run(config(),api.Debug(config=config(),question='查询设备'),None,{},SimpleNamespace(headers={}),revision=3)
            events=[json.loads(event) async for event in response.body_iterator if event.strip()]
            assert events[0]['type']=='progress' and events[-1]['type']=='done'
            assert [e['answer'] for e in events if e.get('answer')][:2]==['完','完成']
            assert events[-1]['revision']==3 and events[-1]['status']=='completed'
    print('PASS: configurable budgets, >6 calls and >4 rounds, conversation context, partial limits/timeouts, repeated calls, real task cancellation for model and MCP, ordered stream completion')


if __name__=='__main__':asyncio.run(main())
