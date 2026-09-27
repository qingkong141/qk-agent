# 应用数据绑定接口

应用配置继续保存为 `StudioArtifact.kind=application`，`schemaVersion=2` 使用 `app/services/application_schema.py` 校验。旧应用配置与快照发布接口保持兼容。

- `POST /api/v1/studio/applications/data`：接受Binding，返回rows/fields/total/truncated、读取时间、来源更新时间和状态。
- `POST /api/v1/studio/applications/debug`：接受完整应用配置，实际读数据并验证字段存在与数值类型，返回组件检查结果。
- 现有应用publish接口对v2执行相同检查，再以expected_revision校验保存发布配置。发布后新草稿不会改动发布配置。
- 应用删除需要停用，DELETE的条件同时检查版本和published_revision，避免并发发布后被删除。

来源为账号内数据服务、实时任务、平台指标或静态JSON。即使数据服务单独设置了免登录，应用绑定仍验证归属。平台模式只能访问配置的PLATFORM_DEVICE_BASE_URL，复用受限分页读取器，不接受任意远端URL，启动/查询凭据不写入应用配置。

每次最多返回1,000条并标记截断，JSON来源最多10,000条/800KB，配置最多30个组件，自动刷新关闭或5～300秒。刷新由打开的页面执行；关闭应用页停止刷新，不创建后台常驻读取任务。实时来源自身后台任务独立运行。

测试：`venv\Scripts\python.exe scripts/test_applications_api.py` 使用隔离内存库验证权限、数据更新、字段检查、发布版本、停止、删除及兼容性，不修改开发库测试数据。
# 多页与交互

schemaVersion 2 增加 pages、variables、navigation、homeTitle，旧配置默认单页。所有页面组件一起校验；页面ID、变量名、组件ID必须唯一，跳转页面、变量和表格传参字段必须有效。发布检查覆盖详情页，不能仅通过首页校验。最多10页，每页30组件、20变量。运行时变量仅在浏览器当前应用实例内，不写入发布配置。
