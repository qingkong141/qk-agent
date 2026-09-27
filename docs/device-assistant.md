# 设备助手接口

前缀 `/api/v1/studio/device-assistant`，全部要求当前用户认证，API Key调用沿用X-End-User-ID隔离。

- GET `/catalog`：当前账号产品与工作台设备。
- GET `/platform-devices?name=...`：固定254设备服务只读目录（最多50台），不接受自定义URL。
- GET `/knowledge`：内置操作与接口知识。
- POST `/ask`：question、context（source、device_id、metric、table）、可选thread_id及expected_revision。AI仅生成结构化方案；分析读取实际来源数据。
- POST `/threads/{thread}/turns/{turn}/execute`：expected_revision。事务中占用版本，再执行已保存的产品/设备创建或报文写入；重复执行拒绝。客户端不能替换已保存方案。
- POST `/devices/{id}/reports`：`{"rows":[{"time":"2026-09-27T05:00:00Z","values":{"battery":61}}]}`。最多100条，按产品物模型校验全部字段。真实写入后台，无自动对254转发。
- GET `/threads`、`/threads/{id}`：当前账号对话和当时分析结果。

复用studio_artifacts表但从通用配置列表排除。产品名称和设备编号通过账号范围的稳定主键保证唯一。报文批次保留，分析最多1,000点，超过时明确要求缩短范围。后台计算趋势、统计和阈值数量；在线模型不能覆盖这些结果。

验证：`python scripts/test_device_assistant.py` 使用独立内存数据库，替换模型输出但实际运行产品、设备、报文、分析和隔离逻辑；在线浏览器联调另验证当前已配置模型的完整流程。
