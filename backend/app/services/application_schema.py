"""Configuration for applications with live, owned data bindings."""
import json
from typing import Literal
from pydantic import BaseModel, Field, JsonValue, model_validator


class PlatformSource(BaseModel):
    table: str = Field(default='m_infusion', pattern=r'^m[a-zA-Z0-9_]{0,119}$')
    device_id: str = Field(default='', max_length=100, pattern=r'^[a-zA-Z0-9_-]*$')
    metric: str = Field(default='metric.infusion.heartBeat.DevicesPower', max_length=300, pattern=r'^metric\.[^.\s]+\.[^.\s]+\.[^.\s]+$')
    lookback_minutes: int = Field(default=15, ge=1, le=1440)


class Binding(BaseModel):
    kind: Literal['snapshot', 'data_service', 'realtime', 'platform'] = 'data_service'
    id: str = Field(default='', max_length=100)
    rows: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=10000)
    platform: PlatformSource = Field(default_factory=PlatformSource)

    @model_validator(mode='after')
    def bounded(self):
        if self.kind in ('data_service', 'realtime') and not self.id.strip():
            raise ValueError('请选择数据来源')
        if self.kind != 'snapshot': self.rows = []
        if len(json.dumps(self.rows, ensure_ascii=False, allow_nan=False).encode()) > 800000:
            raise ValueError('页面数据超过800KB')
        return self


class Action(BaseModel):
    kind: Literal['none','navigate','setVariable','refresh'] = 'none'
    page: str = 'home'
    variable: str = ''
    value: str = Field(default='', max_length=300)
    valueField: str = Field(default='', max_length=300)


class Widget(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal['metric', 'line', 'bar', 'table', 'text','button','select']
    title: str = Field(min_length=1, max_length=80)
    field: str = Field(default='', max_length=300)
    xField: str = Field(default='', max_length=300)
    groupField: str = Field(default='', max_length=300)
    columns: list[str] = Field(default_factory=list, max_length=30)
    filterField: str = Field(default='', max_length=300)
    filterValue: str = Field(default='', max_length=300)
    filterVariable: str = Field(default='', max_length=64)
    variable: str = Field(default='', max_length=64)
    action: Action = Field(default_factory=Action)
    aggregation: Literal['last', 'mean', 'sum', 'count'] = 'last'
    text: str = Field(default='', max_length=2000)
    color: str = Field(default='#b18a61', pattern=r'^#[0-9a-fA-F]{6}$')
    span: Literal[1, 2] = 1
    precision: int = Field(default=2, ge=0, le=6)
    unit: str = Field(default='', max_length=20)

    @model_validator(mode='after')
    def fields(self):
        if not self.title.strip(): raise ValueError('请填写组件标题')
        if self.kind in ('line','bar') and not self.xField: raise ValueError('请选择横轴字段')
        if self.kind in ('line','bar','metric') and not (self.kind=='metric' and self.aggregation=='count') and not self.field:
            raise ValueError('请选择数值字段')
        if self.kind=='table' and not self.columns: raise ValueError('请选择表格列')
        if self.kind=='select' and (not self.field or not self.variable): raise ValueError('请选择下拉框的选项字段与页面变量')
        if self.action.valueField and self.kind!='table': raise ValueError('只有数据表格行点击可以传递行字段值')
        return self


class Page(BaseModel):
    id: str = Field(min_length=1,max_length=100)
    title: str = Field(min_length=1,max_length=80)
    widgets: list[Widget] = Field(default_factory=list,max_length=30)


class Variable(BaseModel):
    name: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,63}$')
    label: str = Field(min_length=1,max_length=80)
    defaultValue: str = Field(default='',max_length=300)


class ApplicationConfig(BaseModel):
    schemaVersion: Literal[2]
    title: str = Field(min_length=1, max_length=120)
    source: Binding
    refreshSeconds: int = Field(default=0, ge=0, le=300)
    widgets: list[Widget] = Field(max_length=30)
    pages: list[Page] = Field(default_factory=list,max_length=9)
    variables: list[Variable] = Field(default_factory=list,max_length=20)
    navigation: Literal['top','left'] = 'top'
    homeTitle: str = Field(default='首页',min_length=1,max_length=80)

    def all_widgets(self):
        return self.widgets+[widget for page in self.pages for widget in page.widgets]

    @model_validator(mode='after')
    def valid(self):
        if not self.title.strip(): raise ValueError('请填写页面标题')
        if self.refreshSeconds and self.refreshSeconds<5: raise ValueError('自动刷新间隔至少5秒')
        widgets=self.all_widgets()
        if len({w.id for w in widgets}) != len(widgets): raise ValueError('组件编号重复')
        pages={p.id for p in self.pages}
        if len(pages)!=len(self.pages) or 'home' in pages: raise ValueError('页面编号重复')
        if any(not p.title.strip() for p in self.pages) or not self.homeTitle.strip(): raise ValueError('请填写页面名称')
        variables={v.name for v in self.variables}
        if len(variables)!=len(self.variables) or variables & {'__proto__','constructor','prototype'}: raise ValueError('变量名称重复或无效')
        for w in widgets:
            if w.filterVariable and (w.filterVariable not in variables or not w.filterField): raise ValueError('筛选变量不存在或未设置筛选字段')
            if w.kind=='select' and w.variable not in variables: raise ValueError('下拉框绑定的变量不存在')
            if w.action.kind=='navigate' and w.action.page not in pages|{'home'}: raise ValueError('跳转页面不存在')
            if w.action.variable and w.action.variable not in variables: raise ValueError('交互引用的变量不存在')
            if w.action.kind=='setVariable' and not w.action.variable: raise ValueError('请选择要设置的变量')
            if w.action.valueField and not w.action.variable: raise ValueError('传递行字段需要选择变量')
        return self
