# 华为云 MaaS 模型接入

2026-09-27 使用账号提供的密钥读取 `GET https://api.modelarts-maas.com/openai/v1/models`，实际返回 12 个模型。密钥通过平台模型管理接口加密保存，不写入代码、文档或智能体配置。

接口地址：`https://api.modelarts-maas.com/openai/v1`。

| 模型 ID（以接口返回值为准） | 接入结果 |
| --- | --- |
| deepseek-v4.1-flash | 可用 |
| deepseek-v4-flash | 可用 |
| deepseek-v4-pro | 可用 |
| openpangu-2.0-pro | 可用 |
| openpangu-2.0-flash | 可用 |
| glm-5.1 | 可用 |
| glm-5.2 | 可用 |
| glm-5.3 | 可用 |
| glm.5.2-arkts-spark | 可用，模型 ID 含点号 |
| kimi-k2.6 | 通过 MaaS Kimi 参数兼容后可用 |
| qwen3-30b-a3b | 当前服务未开启自动工具调用，未加入智能体列表 |
| qwen3-32b | 当前服务未开启自动工具调用，未加入智能体列表 |

“可用”指已完成工具调用、工具结果回传和最终回答验证，不仅是模型目录中存在。Qwen 的工具调用请求返回 HTTP 400 / `ModelArts.81001`，提示服务端需要开启 `enable-auto-tool-choice` 和 `tool-call-parser`。前端配置不能代替服务端启用能力。

上述 10 个模型均通过正常平台登录后的 `/studio/agents/debug` 验证：实际执行 `knowledge_lookup` 查询语义转换配置步骤，工具无错误、运行状态为 `completed`，并返回最终回答。保留原华为云配置，新增 9 个模型配置；模型管理页面已核验。

Kimi 兼容仅作用于 `api.modelarts-maas.com` 上的 `kimi-k2.6`：

- 通过 `extra_body.max_tokens` 发送生成长度限制，避免 SDK 自动改写成 `max_completion_tokens` 后被服务端拒绝。
- 设置 `chat_template_kwargs.thinking=false`，允许现有 SDK 在不回传 `reasoning_content` 的情况下进行工具结果续答。
- SDK 最多重试两次，用于处理实测每秒一次的限流；智能体总执行超时和停止控制仍生效。

其他模型和服务商维持原调用参数。`backend/scripts/test_agent_models.py` 覆盖真实 SDK 请求格式、限流重试、工具续答以及其他服务商不受影响。

模型入口为 AI中心 → 模型管理；智能体配置中的模型下拉框使用同一批账号配置。已有同地址、同模型 ID 的配置复用，不重复创建。
