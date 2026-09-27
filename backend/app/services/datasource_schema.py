from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

TYPES = [
    ('mysql','MySQL',3306),('postgres','PostgreSQL',5432),
    ('polardb-mysql','PolarDB MySQL',3306),('polardb-postgres','PolarDB PostgreSQL',5432),
    ('kafka','Kafka',9092),('roma-mqs','ROMA MQS',9092),('clickhouse','ClickHouse',8123),
    ('iotdb','IoTDB',6667),('hbase','HBase REST',8080),('hive','HiveServer2',10000),
    ('influxdb','InfluxDB 1.x',8086),
]


class Connection(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    type: Literal['mysql','postgres','polardb-mysql','polardb-postgres','kafka','roma-mqs','clickhouse','iotdb','hbase','hive','influxdb']
    host: str = Field(min_length=1,max_length=253,pattern=r'^[a-zA-Z0-9][a-zA-Z0-9.-]*$')
    port: int = Field(ge=1,le=65535)
    database: str = Field(default='',max_length=128,pattern=r'^[a-zA-Z0-9_\-]*$')
    username: str = Field(default='',max_length=128)
    tls: bool = False
    auth: Literal['none','plain','scram-sha-256','scram-sha-512','ldap','nosasl'] = 'none'

    @model_validator(mode='after')
    def auth_mode(self):
        if self.type=='influxdb' and not self.database:
            raise ValueError('InfluxDB请填写数据库名称')
        if self.type in ('kafka','roma-mqs') and self.auth not in ('none','plain','scram-sha-256','scram-sha-512'):
            raise ValueError('Kafka/MQS请选择无认证、PLAIN或SCRAM')
        if self.type=='hive' and self.auth not in ('none','ldap','nosasl'):
            raise ValueError('Hive请选择NONE、NOSASL或LDAP')
        if self.type=='hive' and self.tls:
            raise ValueError('当前Hive适配器使用Thrift端口，暂不支持TLS；需要TLS时请提供连接契约后接入')
        return self


class ReadInput(BaseModel):
    resource: str = Field(min_length=1,max_length=240,pattern=r'^[a-zA-Z0-9_][a-zA-Z0-9_.:-]*$')
    limit: int = Field(default=100,ge=1,le=1000)
    product_field: str = Field(default='productKey',max_length=128,pattern=r'^[a-zA-Z_][a-zA-Z0-9_.:-]*$')
    products: list[str] = Field(default_factory=list,max_length=30)
    lookback_minutes: int = Field(default=60,ge=1,le=43200)

    @model_validator(mode='after')
    def product_size(self):
        if any(not p or len(p)>128 for p in self.products): raise ValueError('产品标识不能为空或超过128字符')
        return self
