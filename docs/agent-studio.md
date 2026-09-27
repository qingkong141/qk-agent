# 智能体开发与MCP

`/api/v1/studio/agents`管理当前账号的智能体草稿，独立于旧版全局agents配置。配置包含model、prompt、services，不存储浏览器令牌或模型密钥。模型从已配置网关`/models`读取，保存和执行均验证白名单。当前网关返回2个模型，两者都已实际调用；至少6个模型的招标数量仍缺4个，不能通过填入名称补足。

## 无代码配置与调用

GET `/catalog`返回模型和MCP服务；POST `/services/{id}/test`通过官方SDK完成initialize及tools/list。POST `/debug`按当前配置执行；POST `/{id}/publish`检查模型目录与选用MCP连接，保存独立发布快照。POST `/{id}/invoke`只运行发布快照；修改草稿不改变已发布内容。停用后无法invoke；删除需先停用。全部数据遵守当前账号及API Key终端用户隔离。

智能体依据工具定义选择调用，最多4轮、6次工具调用、110秒；请求未授权工具会拒绝。调用走真实MCP Streamable HTTP，不将内部函数清单冒充MCP。结果保留工具名称、入参、实际输出和错误；大于16,000字符的模型上下文明确标注截断。最多绘制3张图，数据来自真实工具结果前1,000行，不允许模型自行生成图表数值。

## MCP服务

官方`mcp==1.30.0` SDK，10个独立端点：`/api/v1/studio/mcp/{id}/`（末尾斜杠），使用无状态Streamable HTTP和JSON响应。接受现有平台X-Platform-Token、AI后台Bearer或API Key及X-End-User-ID；不是匿名服务，也不实现新的OAuth授权服务器。会检查Host与Origin，本地仅允许localhost/127.0.0.1及明确配置的CORS来源。迁移到正式域名需同步配置传输层允许Host。

| ID | 能力 |
| --- | --- |
| products | 工作台产品物模型目录 |
| devices | 工作台与254设备目录，按名称/工作台编号查询 |
| telemetry | 真实指标历史，最多1,000点 |
| conditions | 真实趋势、阈值越界、统计和维护建议 |
| knowledge | 5篇平台操作、接口与维护知识 |
| datasets | 数据集目录、文件类型及行数 |
| modeling | 主题域、分层模型与字段 |
| realtime | 实时任务运行状态与最近结果 |
| data-services | 治理结果服务目录与数据 |
| locations | 设备登记位置，不虚构地理坐标 |

所有工具只读，不能控制物理设备。位置服务不等于地理地图/路线服务；实际地图底图和坐标服务仍缺。254时序使用SourceID（如果存在）或DeviceID，不能把资产DeviceCode用于deviceId条件。

验证：`scripts/test_agent_studio.py`使用官方SDK验证10服务握手、实际工具、认证/Origin/账号隔离、模型校验及发布生命周期；在线浏览器验证两种模型、10个实际服务调用、工况图表、发布并invoke。测试智能体和设备记录均保留。

协议参考：[MCP传输规范](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、[官方Python SDK](https://github.com/modelcontextprotocol/python-sdk)。
