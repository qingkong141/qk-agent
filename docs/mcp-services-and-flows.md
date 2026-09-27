# MCP 服务接入与组合编排

## 入口与操作

AI中心下新增“MCP服务管理”和“MCP服务编排”。先连接服务读取真实工具定义，再把工具拖入画布，连线设置执行顺序；参数可填写固定值、引用流程输入或已连线前序节点的结果。

工具参数采用服务返回的 JSON Schema 校验。引用保留对象、数组、数字等原始类型，例如：

```json
{"query":{"$from":"input","path":"/query"}}
```

```json
{"query":{"$from":"n_lookup","path":"/documents/0/title"}}
```

路径采用 JSON Pointer，空路径引用整个对象。循环、断开的节点和引用未连线的前序节点会阻止保存或运行。节点失败、字段缺失时停止后续执行，保留已完成的结果。

“输入与结果”展示每个节点的真实执行结果。“发布API”保存当前版本快照，修改草稿不改变已发布版本；停用后 API 不再执行。API 使用当前平台身份认证，作用域与编辑者账号一致。

## 标准服务接入

- 支持 Streamable HTTP 与 SSE；工具目录分页读取。
- 支持无需认证、Bearer Token、自定义 API Key 请求头。
- 密钥使用服务端 SECRET_KEY 派生密钥加密保存，接口只返回 has_key。修改地址或认证方式必须重新填写密钥；SECRET_KEY 需要稳定保存。
- 外部服务只接收其配置的凭据，不转发平台 Token、平台会话或浏览器 Cookie。
- 内置平台服务继续沿用正常登录身份，可查询设备、时序、工况、知识、位置及业务数据。
- 新服务自动进入智能体的服务目录，同名外部工具在模型调用时使用不同别名，避免串用服务。
- 图像、音频等 MCP 返回内容保留在结果中，当前调试页按 JSON 展示。

本版不启动 stdio 子进程，不实现第三方 OAuth 或 URL 查询参数认证。此类服务需提供 HTTP 端点/适配层后接入；表单里的场景分类不代表相应推理引擎已经部署。

## API

所有路径均位于 `/api/v1` 下；本地前端代理额外加 `/agent-api`。

| 接口 | 用途 |
| --- | --- |
| GET /studio/mcp-services | 当前账号可用的服务 |
| POST /studio/mcp-services | 新增外部服务 |
| PUT /studio/mcp-services/{id} | 修改连接参数，要求 expected_revision |
| DELETE /studio/mcp-services/{id} | 删除未被引用的服务 |
| POST /studio/mcp-services/{id}/test | 建立标准 MCP 会话并发现工具 |
| POST /studio/mcp-services/{id}/call | 按 tool 和 arguments 执行工具 |
| GET/POST /studio/mcp-flows | 查询或创建流程 |
| GET/PUT/DELETE /studio/mcp-flows/{id} | 读取、修改、删除流程 |
| POST /studio/mcp-flows/debug | 执行提交的草稿 config 和 input |
| POST /studio/mcp-flows/{id}/publish | 校验工具并发布快照 |
| POST /studio/mcp-flows/{id}/stop | 停用 API |
| POST /studio/mcp-flows/{id}/invoke | 用 input 执行已发布版本 |

客户端传 `X-Platform-Token`，涉及平台地图接口时同时传 `X-Platform-Session-ID`。页面提供 Python（标准库）及 Java（11+ HttpClient）示例、复制和下载。HTTP 200 后仍须检查返回的 status 是否为 completed；失败结果带 reason 和已完成的 trace。

## 开源方案与获取方式

- [PaddleOCR 官方 MCP](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/integrations/mcp_server.md)：可用于图片文字识别、设备铭牌或文档解析，需要部署识别服务或配置推理 API。
- [高德官方 MCP](https://lbs.amap.com/api/mcp-server/gettingstarted)：申请高德开发者 Key 后使用地图能力，具体配额和接入方式按官方说明配置；其认证方式可能需要适配层。
- [MCP 官方目录](https://registry.modelcontextprotocol.io/)：查找服务及部署说明。目录条目不保证服务免费、可用或已验证。
- 已有语音识别、图像生成或业务 REST API 可以用 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 封装为工具，输出符合 JSON Schema 的结果，再在本平台串联。

开源 MCP 服务与底层模型/API 是两个部分；模型算力、厂商接口和地图 Key 可能仍需另行配置或付费。本阶段未提供外部语音、视觉、多模态生成端点，因此只完成通用接入与编排能力，尚未完成这些外部业务场景的现场联调。

## 验证

2026-09-27：

- `backend/scripts/test_mcp_management.py`：隔离数据库与真实本地 MCP 服务，覆盖 HTTP/SSE 发现和调用、密钥加密、身份隔离、参数验证、前序引用、失败中断、版本冲突、发布快照；用可控模型验证智能体可以调用两个同名外部工具。
- 原有智能体执行、模型管理、智能体管理回归测试通过。
- 前端 `scripts/check-mcp-workflows.cjs`：正常平台登录、搜索、发现工具、真实调用、拖入和移动节点无重复、两步引用、保存刷新、发布并调用 API。生成的数据保留。
- 本地保留流程“平台知识检索与操作查询”，ID `e4b4ba5a-1be0-4aec-a583-83bded8890a9`，已发布 v1。该流程实际调用知识服务两次，并将第一步文档标题传入第二步。
- 前端类型检查和菜单测试通过；未进行生产打包。
- 页面生成的 Python 示例已实际调用已发布流程；Java 示例通过 JDK 17 编译并实际调用成功。Java 显式使用 HTTP/1.1，兼容本地 Vite 代理。
