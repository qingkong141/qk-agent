"""Validated configuration contract for the interactive batch pipeline editor."""
import json
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, FiniteFloat, model_validator
from app.services.datasource_schema import ReadInput


class Node(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal['source','filter','dedup','parse','condition','aggregate','fill','extreme','constant','align','output']
    name: str = Field(min_length=1, max_length=80)
    x: FiniteFloat = Field(ge=0)
    y: FiniteFloat = Field(ge=0)
    field: str = Field(max_length=300)
    value: str = Field(max_length=1000)
    interval: FiniteFloat = Field(ge=1)
    compare: Literal['eq','ne','gt','gte','lt','lte','contains','exists','missing']
    valueType: Literal['number','string','boolean']
    keys: list[str] = Field(max_length=30)
    keep: Literal['first','last']
    parseMode: Literal['json','jsonata']
    script: str = Field(max_length=10000)
    conflict: Literal['error','overwrite']
    outputField: str = Field(max_length=100)
    trueValue: str = Field(max_length=200)
    falseValue: str = Field(max_length=200)

    @model_validator(mode='after')
    def parameters(self):
        if self.kind in ('filter','condition') and not self.field.strip():
            raise ValueError('请选择判断字段')
        if self.kind=='dedup' and not (self.keys or self.field.strip()):
            raise ValueError('请选择去重字段')
        if self.kind=='parse' and not (self.script.strip() if self.parseMode=='jsonata' else self.field.strip()):
            raise ValueError('请填写解析脚本或选择解析字段')
        if self.kind=='condition' and (not self.outputField.strip() or self.outputField in ('__proto__','constructor','prototype')):
            raise ValueError('判断结果字段无效')
        return self


class Edge(BaseModel):
    from_id: str = Field(alias='from')
    to: str


class Query(BaseModel):
    tableName: str = Field(min_length=1, max_length=120, pattern=r'^m')
    startTime: str
    endTime: str
    searchScript: str = Field(max_length=2000)
    pageIndex: int = Field(ge=1)
    pageSize: Literal[20,30,50]


class Source(BaseModel):
    kind: Literal['influx','json','datasource']
    datasource_id: str = Field(default='',max_length=36)
    extraction: ReadInput | None = None
    query: Query
    deviceIds: list[str] = Field(max_length=100)
    mode: Literal['raw','metrics']

    @model_validator(mode='after')
    def dates(self):
        if self.kind=='datasource' and (not self.datasource_id or self.extraction is None):
            raise ValueError('请选择数据源和读取表/主题')
        if self.kind=='influx' and datetime.fromisoformat(self.query.startTime)>=datetime.fromisoformat(self.query.endTime):
            raise ValueError('结束时间必须晚于开始时间')
        return self


class PipelineConfig(BaseModel):
    schemaVersion: Literal[2]
    nodes: list[Node] = Field(min_length=2,max_length=30)
    edges: list[Edge] = Field(max_length=30)
    input: str = Field(max_length=600000)
    source: str = Field(max_length=1000)
    chartField: str = Field(max_length=300)
    sourceSettings: Source

    @model_validator(mode='after')
    def graph_and_input(self):
        rows=json.loads(self.input)
        if not isinstance(rows,list) or len(rows)>1000 or any(not isinstance(row,dict) for row in rows):
            raise ValueError('输入应为最多 1000 条的对象数组')
        ids={n.id for n in self.nodes}
        sources=[n for n in self.nodes if n.kind=='source']; outputs=[n for n in self.nodes if n.kind=='output']
        if len(ids)!=len(self.nodes) or len(sources)!=1 or len(outputs)!=1:
            raise ValueError('节点重复，或缺少唯一的数据输入和输出')
        if any(e.from_id not in ids or e.to not in ids or e.to==sources[0].id or e.from_id==outputs[0].id for e in self.edges):
            raise ValueError('连线端点无效')
        seen=set();current=sources[0].id
        while current:
            if current in seen:
                raise ValueError('管道不能包含循环')
            seen.add(current)
            following=[e.to for e in self.edges if e.from_id==current]
            if len(following)>1 or sum(e.to==current for e in self.edges)>1:
                raise ValueError('当前管道不支持分叉或合流')
            if not following and current!=outputs[0].id:
                raise ValueError('流程尚未连接到输出')
            current=following[0] if following else None
        if len(seen)!=len(ids):
            raise ValueError('有未连接的节点')
        return self
