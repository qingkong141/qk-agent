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


class Widget(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal['metric', 'line', 'bar', 'table', 'text']
    title: str = Field(min_length=1, max_length=80)
    field: str = Field(default='', max_length=300)
    xField: str = Field(default='', max_length=300)
    groupField: str = Field(default='', max_length=300)
    columns: list[str] = Field(default_factory=list, max_length=30)
    filterField: str = Field(default='', max_length=300)
    filterValue: str = Field(default='', max_length=300)
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
        return self


class ApplicationConfig(BaseModel):
    schemaVersion: Literal[2]
    title: str = Field(min_length=1, max_length=120)
    source: Binding
    refreshSeconds: int = Field(default=0, ge=0, le=300)
    widgets: list[Widget] = Field(max_length=30)

    @model_validator(mode='after')
    def valid(self):
        if not self.title.strip(): raise ValueError('请填写页面标题')
        if self.refreshSeconds and self.refreshSeconds<5: raise ValueError('自动刷新间隔至少5秒')
        if len({w.id for w in self.widgets}) != len(self.widgets): raise ValueError('组件编号重复')
        return self
