# 架构优化计划

> 基于当前代码状态重新校准后的执行版。目标不是一次性引入所有主流架构能力，而是优先补齐会直接影响产品可用性、质量可信度和后续迭代效率的缺口。

## 当前判断

项目核心抽象是成立的：LangGraph 编排、场景 Agent、临床决策表、RAG、工具注册、WebSocket 流式输出都已经有了基本骨架。当前真正的问题不是“架构不够高级”，而是有几个能力没有形成闭环：

- `AgentConfig` 有 CRUD，但运行时不读取，配置目前像“死数据”。
- 临床 Agent 和 RAG 输出缺少稳定回归评估，模型或 Prompt 一改就很难知道质量有没有漂移。
- LangGraph checkpoint 没启用，长流程中断恢复和 human-in-the-loop resume 还没有基础设施。
- 观测、MCP、GraphRAG 都有价值，但当前不是最短板。

## 优先级


| 优先级 | 项目                   | 当前结论               | 行动    |
| --- | -------------------- | ------------------ | ----- |
| P0  | AgentConfig 运行时接入    | 基础运行时接入已完成，前端选择器待补 | 继续完善  |
| P0  | 轻量评估集 + 确定性回归        | 临床/RAG/工具路由需要可信回归  | 立即做   |
| P1  | 最小 LLM-as-Judge      | 有价值，但先小规模校准        | 评估集后做 |
| P1  | LangGraph checkpoint | 对长流程和中断恢复有价值       | 第二阶段做 |
| P2  | 真实 token/cost 归因     | 有助运营和排查            | 后续补   |
| P2  | MCP                  | 当前内部工具足够           | 暂不做   |
| P2  | GraphRAG / 知识图谱      | 方向对，但复杂度高          | 暂不做   |




## 一、P0 - AgentConfig 运行时接入



### 问题

[backend/app/api/agents.py](../backend/app/api/agents.py) 已经提供 Agent 配置 CRUD，[backend/app/models/agent_config.py](../backend/app/models/agent_config.py) 也有 `agent_configs` 表，但 [backend/app/core/agent_factory.py](../backend/app/core/agent_factory.py) 创建运行时 Agent 时没有读取这些配置。

结果是：

- 用户创建/修改 Agent 配置后，对真实对话没有影响。
- `is_default` 标记没有运行时意义。
- `tools` 字段不会限制或选择工具。



### 当前进展

基础运行时接入已完成：

- [backend/app/core/agent_factory.py](../backend/app/core/agent_factory.py) 新增 DB 配置读取和 `create_agent_executor_from_db`。
- [backend/app/schemas/chat.py](../backend/app/schemas/chat.py) 支持可选 `agent_id`。
- [backend/app/api/chat.py](../backend/app/api/chat.py) 的单 Agent HTTP/WS 对话会读取默认或指定 Agent 配置。
- [sdk/src/types/index.ts](../sdk/src/types/index.ts)、[sdk/src/client/ChatClient.ts](../sdk/src/client/ChatClient.ts)、[sdk/src/stream/StreamHandler.ts](../sdk/src/stream/StreamHandler.ts) 已支持 `agentId`。

剩余工作：

- 前端 Chat 页面增加 Agent 选择器。
- workflow/临床场景是否允许外部 AgentConfig 覆盖，需要单独设计，暂不混入。
- 增加 API 级集成测试，验证真实 `/chat` 请求可选择 Agent。



### 目标

让“默认 Agent 配置”和“指定 Agent 配置”都能影响单 Agent 运行时：

- 未传 `agent_id` 时，读取 `is_default=True` 的配置。
- 传入 `agent_id` 时，读取对应配置。
- `role`、`capabilities`、`system_prompt`、`tools` 生效。
- 配置缺失时回退到当前默认行为。



### 建议实现

新增异步工厂函数，不破坏现有同步 `create_agent_executor`：

```python
async def create_agent_executor_from_db(
    db: AsyncSession,
    *,
    agent_id: str | None = None,
    streaming: bool = False,
) -> AgentExecutor:
    config = await load_agent_config(db, agent_id)
    role = config.role if config else DEFAULT_ROLE
    capabilities = config.capabilities if config else DEFAULT_CAPABILITIES
    system_prompt = config.system_prompt if config and config.system_prompt else None
    tool_names = json.loads(config.tools) if config and config.tools else None
    return create_agent_executor(
        role=role,
        capabilities=capabilities,
        system_prompt=system_prompt,
        tool_names=tool_names,
        streaming=streaming,
    )
```

同时需要让 `create_agent_executor` 支持可选 `system_prompt` 覆盖。

### 验证

- 单元测试：默认配置存在时被读取。
- 单元测试：指定 `agent_id` 时读取指定配置。
- 单元测试：`tools` 为空/非法工具名时行为可控。
- API 或 chat 路径测试：对话请求能选择 Agent 配置。



## 二、P0 - 轻量评估集与确定性回归



### 问题

当前项目有日志和部分规则测试，但缺少覆盖 Agent 行为的稳定评估集。风险集中在：

- supervisor intent 路由漂移。
- RAG 命中/未命中判断漂移。
- `EducationAgent` 推荐结构变化。
- `InfusionAgent` 安全阻断遗漏。
- `ClinicalAgent.run()` 输出字段不完整。



### 目标

先建立一个不依赖 LLM Judge 的最小回归体系，用 pytest 跑确定性断言。

建议从 20-50 条黄金用例开始，覆盖：


| 类型       | 示例                        | 断言                                   |
| -------- | ------------------------- | ------------------------------------ |
| 意图路由     | “帮我算 20ml/h 调到 40ml/h 增幅” | intent=calculation 或 infusion_adjust |
| RAG      | 上传知识库后问文档内问题              | `kb_hit=True` 且 sources 非空           |
| RAG miss | 问完全无关主题                   | 返回知识库未命中                             |
| 输液安全     | 大幅调速、缺设备 ID、缺当前速率         | `risk_assessment.blocked=True`       |
| 宣教推荐     | 明确诊断文本                    | 生成 pending items                     |
| 决策状态     | confirm/skip/cancel       | 状态迁移正确                               |




### 建议实现

- 新增 `backend/tests/golden_cases/` 存放 JSON 测试样例。
- 新增 `backend/tests/test_agent_regression.py`。
- 对 LLM 依赖强的地方优先 mock LLM 返回，先测管线和规则。
- 对 RAG 使用小型测试文档或直接构造向量检索结果。



### 验证

每次改 Agent、Prompt、RAG、risk_engine 前后都能跑：

```bash
backend\venv\Scripts\python.exe -m pytest backend\tests
```



## 三、P1 - 最小 LLM-as-Judge



### 判断

LLM-as-Judge 需要做，但不建议一开始上完整平台、生产采样和发布门禁。当前更适合“低成本校准版”。

### 第一版只评三件事


| 维度   | 评判对象               | 输出        |
| ---- | ------------------ | --------- |
| 忠实性  | 回复是否只基于输入和检索内容     | pass/fail |
| 临床安全 | 是否遗漏风险阻断、是否越权给执行医嘱 | pass/fail |
| 完整性  | 是否遗漏关键字段或确认提示      | pass/fail |




### 原则

- 一次只评一个维度。
- 使用 pass/fail/unclear，不用 1-5 分。
- `temperature=0`。
- 未和人工样本校准前，不做 CI 门禁。
- Judge 结果先作为报告，不阻断发布。



### 延后项

以下内容等项目量级上来后再做：

- 生产流量 5-20% 采样。
- 200-500 条人工标注集。
- Cohen's kappa 校准。
- 自动发布门禁。



## 四、P1 - LangGraph checkpoint



### 当前状态

[backend/app/graph/workflow.py](../backend/app/graph/workflow.py) 中 workflow 最后直接 `graph.compile()`，没有配置 checkpointer。

### 什么时候需要做

满足任意一个条件时就值得做：

- 临床流程开始支持中断确认后继续执行。
- 单次 workflow 包含多次 LLM/tool 调用，失败重跑成本明显。
- 需要排查某个节点的历史状态。
- WebSocket 断连后要恢复原流程。



### 建议方案

先用数据库持久化 checkpoint，不单独引 Redis：

```python
graph.compile(checkpointer=checkpointer)
```

调用时以 `conversation_id` 或 `decision_id` 作为 `thread_id`。

### 注意

LangGraph checkpoint 会影响调用方式、状态序列化和测试方式，不建议在评估集之前做。先有回归测试，再改 workflow 生命周期。

## 五、P2 - 可观测性和成本归因

当前已有 `UsageLog`，但 [backend/app/services/usage_tracker.py](../backend/app/services/usage_tracker.py) 主要是估算 token，不是真实用量。

后续可以补：

- 从 LLM response metadata 读取真实 input/output token。
- 给 `UsageLog` 增加 provider、model、latency_ms、cost 字段。
- 按 conversation、agent_name、tool_name 聚合。
- 慢节点和工具失败率告警。

这项有运营价值，但不是当前功能闭环的第一短板。

## 六、P2 - MCP

暂不做。

当前工具通过 [backend/app/tools/**init**.py](../backend/app/tools/__init__.py) 统一注册，数量有限且全部内部维护，现有 ToolManager 足够。

引入 MCP 的触发条件：

- 要把临床工具开放给外部 AI 应用。
- 要接大量第三方系统工具。
- 多团队独立维护工具，需要协议边界。



## 七、P2 - GraphRAG / 知识图谱

暂不做。

临床领域确实适合实体关系建模，例如患者、疾病、用药、过敏、设备之间的关系。但当前更应该先把基础 RAG 做稳：

- PDF 文本和表格抽取。
- 来源引用。
- 命中/未命中质量。
- 检索回归测试。

GraphRAG 可以等知识库规模扩大、实体关系查询成为明确痛点后再引入。

## 推荐迭代顺序

```text
第 1 阶段：AgentConfig 运行时接入
验证：配置能真实影响 role/capabilities/system_prompt/tools

第 2 阶段：轻量评估集
验证：20-50 条黄金用例稳定通过，覆盖路由/RAG/临床安全/决策状态

第 3 阶段：最小 LLM-as-Judge
验证：对忠实性、临床安全、完整性输出 pass/fail 报告

第 4 阶段：LangGraph checkpoint
验证：conversation_id/thread_id 可恢复状态，失败节点不必全流程重跑

后续按需：真实 token 成本、MCP、GraphRAG、OpenTelemetry
```



## 近期成功标准

短期不以“接入多少新框架”为目标，而以这几个结果为准：

- 创建的 Agent 配置能被真实对话使用。
- 每次改 Agent/RAG/临床规则后，有一组测
- RAG 未命中不会被包装成确定答案。
- 后续再引入 checkpoint/Judge 时，有测试保护现有行为。试能告诉我们有没有退化。
- 高风险临床建议不会绕过 `risk_engine`。

