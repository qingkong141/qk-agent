# 架构优化计划

> 基于主流 Agent 架构对照分析，梳理本项目待优化项。
> 评估维度：**安全、可观测性、工程健壮性、标准化程度、可扩展性**。

---

## 优先级分类


| 标记        | 含义                 | 行动   |
| --------- | ------------------ | ---- |
| 🔴 **P0** | 架构短板，直接影响质量可信度     | 尽快启动 |
| 🟡 **P1** | 工程健壮性不足，特定场景有风险    | 纳入迭代 |
| 🟢 **P2** | 锦上添花，当前够用，未来扩展时有价值 | 按需做  |
| ✅ **已对齐** | 与主流架构一致或领先         | 维持   |


---

## 一、🔴 P0 — 缺少 LLM-as-Judge 评估管线

### 问题

当前项目有可观测性（LangSmith + 自定义日志），但**没有自动化质量评估**。Agent 输出质量的变化完全依赖人工感知，存在以下风险：

- `analyze()` 输出质量漂移 → 无人知晓
- `reflect()` 策略调整 → 无法量化效果
- 模型切换（小模型 → 大模型）→ 没有回归对比
- 89% 的团队有可观测性，但只有 52% 有自动化评估——本项目属于有观测无评估的 37%

### 方案

搭建三层评估架构：

```
Layer 3: 人工校准（每周 / 每迭代）
  规模: 200-500 条标注样本
  用途: 校准 Judge 的基准真相

Layer 2: LLM-as-Judge（每 PR / 每夜）
  规模: 采样 5-20% 生产流量 + 全部异常 Trace
  工具: LangSmith / Langfuse
  用途: 语义质量判分（忠实性、完整性、安全性）

Layer 1: 确定性检查（每次提交 / 每次调用）
  规模: 100% 流量
  工具: pytest + Pydantic validators
  用途: Schema / PII / 超时 / 安全等级合规
```



### 评估维度


| 评估维度           | 评判对象                   | 评判标准               | 层级      |
| -------------- | ---------------------- | ------------------ | ------- |
| JSON Schema 合规 | `analyze()` 输出         | Pydantic 校验        | Layer 1 |
| 工具选择正确性        | RAG / 计算器路由            | 工具名白名单匹配           | Layer 1 |
| 安全等级合规         | `safety_level` 产出      | 规则引擎复核             | Layer 1 |
| 输液速率变更范围       | `InfusionAgent` 建议     | `risk_engine` 阈值检查 | Layer 1 |
| 反思去重率          | `reflect()` 前后对比       | 去重比例统计             | Layer 1 |
| 临床提取完整度        | `extracted_info`       | LLM Judge vs 人工标注  | Layer 2 |
| 宣教推荐相关性        | `EducationAgent.items` | LLM Judge 评分       | Layer 2 |
| 输液建议合理性        | `InfusionAgent` 建议理由   | LLM Judge 评分       | Layer 2 |
| 轨迹完整性          | 整个 `run()` 八步管线        | LLM Agent Judge    | Layer 2 |




### 实施步骤

```
Phase 1: 地基（第 1-2 周）
├─ 接入 LangSmith / Langfuse 自动 Trace
├─ 编写 Layer 1 pytest 断言套件
│  ├─ Schema 校验（Pydantic validators）
│  ├─ safety_level 合规检查
│  ├─ reflect() 去重率统计
│  └─ InfusionAgent rate 变化范围校验
└─ 准备 50 条手工标注的黄金测试集

Phase 2: LLM Judge（第 3-4 周）
├─ 为每类 Agent 定义专属 Judge Prompt
├─ 跑第一批 Judge 评分
├─ 人工校准 → 计算 Cohen's κ → 调整 Prompt
└─ 集成到 CI：每个 PR 跑 Judge 套件

Phase 3: 持续运行（第 5 周起）
├─ 生产流量采样（每天 50-100 条）→ 自动 Judge
├─ 低分 Trace 自动入库 → 黄金数据集增长
├─ 每周校准 → 每月全量回归
└─ 发布门禁：Judge Score < 0.85 且下降 >5% → 拦截
```



### Judge Prompt 核心原则

- **一次评判一个维度**（不要一个 Prompt 同时评忠实性 + 流畅性 + 相关性）
- **用二值或三值量表**（Pass/Fail 比 1-5 分更可靠）
- **Judge 和 Worker 使用不同模型族**（GPT 评 Claude，Claude 评 GPT；同族评分偏高 10-25%）
- **temperature=0**（Judge 必须可复现）
- **未校准的 Judge 不能做门禁**（Cohen's κ ≥ 0.84 才可信）



### 成本估算


| 项目                 | 量级               | 月成本            |
| ------------------ | ---------------- | -------------- |
| Layer 1 确定性断言      | 100% 流量          | $0             |
| Layer 2 LLM Judge  | 采样 10% ~1,000次/天 | $150-500       |
| Layer 3 人工校准       | 50条/周            | 人力 2-4h/周      |
| LangSmith/Langfuse | 基础用量             | $0-199         |
| **合计**             |                  | **$150-700/月** |


---



## 二、🟡 P1 — LangGraph Checkpoint 断点恢复未启用



### 问题

LangGraph 原生支持 checkpoint（Postgres/Redis 持久化），当前项目未配置。后果：

- `ClinicalAgent.run()` 八步管线在第 7 步失败 → 从头执行，浪费 LLM 调用成本
- 工作流中途断开（WebSocket 断连、服务器重启）→ 状态丢失
- 无法实现"time-travel"调试（回退到某个节点重新执行）



### 方案

```python
# graph/workflow.py
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver(
    conn_string=settings.DATABASE_URL,
    pool_size=5,
)

graph = workflow.compile(checkpointer=checkpointer)

# 调用时传入 thread_id
result = await graph.ainvoke(
    state,
    config={"configurable": {"thread_id": conversation_id}},
)
```



### 附加收益

- 支持 Human-in-the-Loop `interrupt()` + 确认后 `resume()`（临床场景天然需求）
- 支持 `graph.get_state(thread_id)` 查询当前状态
- 支持 `graph.get_state_history(thread_id)` 回溯执行历史

---



## 三、🟡 P1 — AgentConfig CRUD 与运行时脱节



### 问题

`[api/agents.py](backend/app/api/agents.py)` 提供完整的 Agent 配置 CRUD（`agent_configs` 表），但运行时完全不读取这些配置：

- 创建了自定义 Agent 配置 → 对话中无法使用
- `is_default` 标记 → 对运行时无影响
- 配置表数据 → 纯粹是数据库里的死数据



### 方案

方案 A（轻量）：在 `core/agent_factory.py` 的 `create_agent_executor` 中读取 `agent_configs` 表：

```python
async def create_agent_executor_from_db(
    agent_id: str,
    db: AsyncSession,
    ...
):
    config = await db.get(AgentConfig, agent_id)
    role = config.role if config else DEFAULT_ROLE
    capabilities = config.capabilities if config else DEFAULT_CAPABILITIES
    tools = json.loads(config.tools) if config.tools else None
    ...
```

方案 B（对齐临床场景）：将 AgentConfig 与 ClinicalAgent 子类关联：

```python
class ClinicalAgent:
    agent_config_id: str | None = None  # 绑定到 DB 配置

    async def load_config(self, db: AsyncSession):
        config = await db.get(AgentConfig, self.agent_config_id)
        self.system_prompt = config.system_prompt or self.system_prompt
        self.model_name = config.model_name or self.model_name
```

---



## 四、🟢 P2 — 工具系统未接入 MCP 协议



### 问题

当前工具通过 `ToolManager` 单例注册，工具定义与外部世界隔离：

```python
# 现状：自研注册
tool_manager.register("search_knowledge_base", search_kb_fn)
```



### 是否现在该做？

**不着急。** MCP 解决的核心问题是"跨系统工具的标准化接入"（M×N 集成问题）。本项目工具全部内部研发、有限、协议统一——M×N 问题不存在。现有 `ToolManager` 完全够用。数量

### 何时值得引入

- 需要把临床工具（宣教推荐、输液评估）开放给外部 AI 应用调用时
- 需要接入大量第三方工具（Slack、Jira、电子病历系统）时
- 团队拆分后工具由不同组独立维护时



### 接入路径

```python
# 将现有工具包装为 MCP Server
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("clinical-tools")

@mcp.tool()
async def recommend_education(patient_text: str) -> dict:
    agent = EducationAgent()
    return await agent.analyze(patient_text, ...)

@mcp.tool()
async def assess_infusion_risk(device_id: str, rate: float) -> dict:
    return assess_infusion_risk(device_id, rate)
```

---



## 五、🟢 P2 — 可观测性可加强



### 当前状态

- ✅ LangSmith 集成（trace 自动采集）
- ✅ `LLMLoggingCallback`（LLM 调用日志 + PII 脱敏）
- ✅ `UsageLog` 表（用量记录）
- ⚠️ 缺少 OpenTelemetry 标准化
- ⚠️ 缺少成本归因（每次对话的 LLM 花费）
- ⚠️ 缺少节点级延迟监控



### 建议补充


| 指标                 | 方案                                          |
| ------------------ | ------------------------------------------- |
| 每次调用 token 消耗 + 费用 | 从 LLM response metadata 中提取 + 写入 `UsageLog` |
| 节点级延迟              | LangSmith / OpenTelemetry 自动采集              |
| Agent 成功率          | 依赖 P0 评估管线建立后统计                             |
| 告警规则               | Tool 调用失败率 > 5%、平均延迟突增 2x                   |


---



## 六、🟢 P2 — 缺少 GraphRAG / 知识图谱记忆



### 当前状态

- ✅ 短期记忆：`Message` 表（对话历史，最近 40 条）
- ✅ 长期记忆：`LongTermMemory` 表 + `save_memory`/`recall_memory` 工具
- ⚠️ 缺少结构化知识表示（实体-关系图谱）



### 场景价值

临床场景天然适合知识图谱：患者-疾病-用药-过敏之间的实体关系，比向量相似度更适合精确溯因。

### 建议

当前阶段向量检索 RAG 足够，GraphRAG 在后续引入 Neo4j 或 Neo4j 替代方案时再考虑。

---



## 七、✅ 已对齐——不需要改的部分


| 能力                            | 评价                                      |
| ----------------------------- | --------------------------------------- |
| **Supervisor + 多 Agent 编排**   | LangGraph 图结构，业界主流方案                    |
| **ClinicalAgent 管线（八步）**      | 抽象清晰，扩展只需覆写 `analyze()` + `execute()`   |
| **LLM 反思 + 确定性规则双重安全检查**      | 超越多数主流项目                                |
| **Human-in-the-Loop 临床决策状态机** | 设计成熟，审计追踪完整                             |
| **多模型分层**                     | `model_name` 可指定不同模型，灵活                 |
| **Prompt 注入防御**               | `<user_input>` 标签隔离，有效                  |
| **WebSocket 流式推送**            | 业界标配                                    |
| **多 LLM 供应商支持**               | Anthropic / OpenAI / Ollama / DashScope |
| **工具-决策联动**                   | 工具调用自动创建 Decision 记录，设计精妙               |


---



## 总结——建议迭代顺序

```
第 1-2 周:  🔴 Layer 1 确定性断言 + 黄金测试集构建
第 3-4 周:  🔴 Layer 2 LLM Judge + CI 集成
第 5-6 周:  🔴 LLM Judge 校准 → 发布门禁
第 7-8 周:  🟡 Checkpoint 断点恢复
第 9-10 周: 🟡 AgentConfig 运行时打通
后续按需:   🟢 MCP / GraphRAG / OpenTelemetry
```

> **核心原则**：架构的好坏不取决于用了多少新标准，而取决于"改一行代码"要做多少改动。当前项目改动半径小、核心抽象正确——这比引入任何新协议都有价值。

