# 主索引与数据源接口

主索引：`/api/v1/studio/master-index`，GET列表/POST新增，`/{id}` GET/PUT/DELETE，`/rules`编码规则，`/export`映射CSV。MasterIndex/MasterAlias独立表，数据库唯一约束阻止同一数据域重复绑定。PUT/DELETE需要expected_revision；事务保证别名冲突回滚。字段登记内容是系统快照，不虚构HIS实时接入。

数据源：`/api/v1/studio/datasources`，GET列表/POST新增，`/{id}` GET/PUT/DELETE，`/types`十类型，`/{id}/test`POST测试、`/resources`GET目录、`/extract`POST抽取。DataSource独立表，配置不包含明文密码，Fernet密钥由稳定SECRET_KEY派生；API不返回密文或明文。

每次抽取启动独立Python进程，stdin传入私密配置，stdout仅返回JSON结果；超时30秒杀死进程，最多4个同时运行。配置/查询均受认证与归属检查；仅生成只读查询，表名校验、产品值绑定。Kafka不提交位点；IoTDB/HBase/Kafka产品筛选只覆盖当前读取窗口。最大1000条/600KB，Decimal保持字符串以免精度损失，二进制标注base64。接口不具备全量持续同步语义。

依赖在requirements.txt锁定。Hive使用纯Python SASL，支持NONE/NOSASL/LDAP Thrift；不含Kerberos/TLS。HBase要求REST网关；IoTDB目前树模型；证书使用操作系统/驱动默认信任，私有CA需环境配置。

运行`scripts/test_master_index.py`与`scripts/test_datasources.py`：前者验证关联一致性和隔离，后者验证驱动导入、只读参数、真实子进程到隔离HTTP服务的数据读取、权限、密码加密、连接失败。不会修改开发库。

尚无用户提供的十类服务，隔离测试不等于真实服务验收。需要地址、只读凭据、版本、表/主题、产品字段和认证配置。现有254时序接口独立保留。
