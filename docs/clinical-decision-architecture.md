# 临床决策框架 — 架构方案

> 版本：v2.0 | 日期：2026-07-15 | 状态：待评审

---

## 1. 概述

### 1.1 核心模式

> **临床事件 → AI 理解 → 推荐操作 → 人工确认 → 执行系统指令**

这是一个通用的"人机协同决策"模式。AI 负责理解和推荐，人负责确认，业务系统负责执行。覆盖的场景包括但不限于：

| 场景 | 触发 | 推荐 | 执行 |
|---|---|---|---|
| **宣教推送**（Phase 1） | 诊断下达 / 用户询问 | 匹配宣教内容 | 宣教系统推送 |
| **输液滴速调整**（Phase 2） | 体征告警 / 用户指令 | 建议滴速值 | 输液泵 API |
| **用药提醒**（未来） | 新开处方 | 用药指导宣教 | 宣教系统推送 |
| **检查推荐**（未来） | 诊断 + 症状 | 推荐检查项目 | HIS 开单 |

### 1.2 设计原则

1. **AI 做推荐，人做决策，业务系统做执行** —— 三层各管各的，权责边界清晰
2. **每个场景独立工作流** —— 宣教推错了是信息冗余，输液调错了是医疗事故，不能混在一起
3. **决策过程可追溯** —— 谁在什么时候确认了什么、跳过了什么，全部落库

### 1.3 与现有系统的关系

```
┌───────────────────────────────────────────────┐
│              AI Agent（本方案新增）              │
│                                                │
│  临床事件 → 意图路由 → 场景Agent → 决策记录       │
│  "理解临床文本，推荐操作方案"                      │
└────────────────────┬──────────────────────────┘
                     │ 调用各业务系统 API
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ 宣教系统   │  │ 输液泵系统 │  │  ...     │
│ (已有)    │  │ (已有)    │  │          │
└──────────┘  └──────────┘  └──────────┘
```

---

## 2. 系统架构

### 2.1 整体架构图

```
                          ┌────────────────────┐
                          │  HIS / EMR / 监护仪  │
                          │ (诊断、体征、处方...)  │
                          └────────┬───────────┘
                                   │ ① Webhook / API
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                       AI Agent 平台                                │
│                                                                   │
│  ┌────────────────────┐    ┌────────────────────┐                 │
│  │  POST /clinical     │    │   聊天对话触发        │                │
│  │  -events            │    │   (自然语言输入)      │                │
│  └────────┬───────────┘    └────────┬───────────┘                 │
│           │                         │                              │
│           ▼                         ▼                              │
│  ┌───────────────────────────────────────────────┐                │
│  │              supervisor_node                    │                │
│  │  分析 intent → 路由到对应场景 agent               │                │
│  └───────┬───────────┬───────────┬───────────────┘                │
│          │           │           │                                  │
│          ▼           ▼           ▼                                  │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐                        │
│  │ education │ │ infusion  │ │  future   │   ← 每个场景独立 agent   │
│  │ _agent    │ │ _agent    │ │ _agent    │                        │
│  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘                        │
│        │             │             │                                │
│        │    ┌────────┴────────┐    │                                │
│        │    │ 写入 clinical_decisions │  ← 通用决策表                │
│        │    │ action_type 区分场景    │                              │
│        │    └────────┬────────┘    │                                │
│        │             │             │                                │
│        ▼             ▼             ▼                                │
│  ┌───────────────────────────────────────────────┐                 │
│  │              response_agent                    │                 │
│  │  各 agent 的结果统一合成回复 + 确认提示          │                 │
│  └────────────────────┬──────────────────────────┘                 │
│                       │                                            │
│                       ▼                                            │
│  ┌───────────────────────────────────────────────┐                 │
│  │      WebSocket → 前端渲染确认卡片               │                 │
│  │      用户确认 → Agent 调用对应 Tool              │                 │
│  └────────────────────┬──────────────────────────┘                 │
│                       │ ② 调用业务系统 API                          │
└───────────────────────┼──────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌────────────┐  ┌────────────┐  ┌────────────┐
│  宣教系统    │  │  输液泵系统  │  │   ...      │
└────────────┘  └────────────┘  └────────────┘
```

### 2.2 模块结构

```
backend/app/
├── api/
│   ├── chat.py                  # 现有，扩展 intent 路由
│   ├── clinical_events.py       # 🆕 统一临床事件入口
│   └── clinical_decisions.py    # 🆕 通用决策 API
├── graph/
│   ├── workflow.py              # 修改，动态路由到场景 agent
│   ├── nodes.py                 # 修改，新增各场景 agent node
│   ├── state.py                 # 修改，新增通用字段
│   └── agents/                  # 🆕 各场景 agent 实现
│       ├── __init__.py
│       ├── base.py              # 场景 agent 基类
│       ├── education.py         # action_type = education_push
│       └── infusion.py          # action_type = infusion_adjust
├── models/
│   └── clinical_decision.py     # 🆕 通用临床决策表
├── schemas/
│   └── clinical.py              # 🆕 通用请求/响应模型
├── services/
│   └── clinical_decision_service.py  # 🆕 通用决策编排
└── tools/
    ├── education_tools.py       # 宣教场景 Tool
    └── infusion_tools.py        # 🆕 输液场景 Tool
```

---

## 3. 通用数据模型

### 3.1 `clinical_decisions` 表

```sql
CREATE TABLE clinical_decisions (
    id                VARCHAR(36) PRIMARY KEY,
    conversation_id   VARCHAR(36),           -- 关联对话
    patient_id        VARCHAR(64)  NOT NULL, -- 患者标识
    patient_name      VARCHAR(128),          -- 患者姓名（冗余）
    action_type       VARCHAR(32)  NOT NULL, -- 🆕 场景类型
                                             -- 'education_push' | 'infusion_adjust' | ...
    trigger_source    VARCHAR(32)  NOT NULL, -- 'chat' | 'his_webhook' | 'vital_alert'
    trigger_text      TEXT         NOT NULL, -- 原始触发文本
    extracted_info    JSONB,                 -- LLM 提取的结构化上下文
    action_params     JSONB        NOT NULL, -- 🆕 场景特定的参数
                                             -- education_push: {items: [{id, title, score, reason, status}]}
                                             -- infusion_adjust: {current_rate, suggested_rate, reason, device_id}
    status            VARCHAR(20)  NOT NULL DEFAULT 'pending',
                                             -- pending | partial | resolved | expired
    confirmed_by      VARCHAR(36),           -- 确认人
    last_confirmed_at TIMESTAMP,
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW(),

    INDEX idx_patient (patient_id),
    INDEX idx_action_type (action_type),
    INDEX idx_status (status),
    INDEX idx_conversation (conversation_id),
    INDEX idx_created (created_at)
);
```

### 3.2 状态机（所有场景通用）

```
                    ┌──────────┐
                    │ pending  │ ← AI 刚分析完
                    └────┬─────┘
                         │
              用户部分确认/执行
                         │
                    ┌────▼────┐
                    │ partial │ ← 还有未处理的条目/子项
                    └──┬──┬──┘
                       │  │
        再次确认/跳过    │  │ 7 天无操作
        处理完剩余       │  │
                       │  │
              ┌────────▼──▼────────┐
              │     resolved       │ ← 全部已处理
              └────────────────────┘
```

### 3.3 状态聚合规则

```python
def compute_status(items: list[dict]) -> str:
    """各场景的 action_params 中包含 items，每个 item 有独立 status"""
    statuses = {item["status"] for item in items}
    if all(s in ("executed", "skipped", "failed") for s in statuses):
        return "resolved"
    if any(s == "pending" for s in statuses):
        if any(s in ("executed", "skipped") for s in statuses):
            return "partial"
        return "pending"
    return "resolved"
```

---

## 4. 场景详解

### 4.1 场景一：宣教推送（`action_type = education_push`）

#### 4.1.1 数据结构

```json
// action_params
{
  "items": [
    {
      "id": "E001",
      "title": "糖尿病饮食指南",
      "type": "video",
      "score": 0.95,
      "reason": "HbA1c 8.5% 急需饮食干预",
      "status": "pending"
    }
  ],
  "department": "内分泌科"
}
```

#### 4.1.2 对话触发流程

```
用户: "患者张三，诊断：2型糖尿病，HbA1c 8.5%，需要宣教"
  │
  ▼
supervisor → intent: education_recommend
  │
  ▼
education_agent:
  1. LLM 提取: diagnosis=["2型糖尿病"], indicators={"HbA1c":"8.5%"}
  2. LLM 生成检索关键词: ["糖尿病饮食管理","血糖控制",...]
  3. 调用宣教系统 GET /education/search → 匹配内容
  4. LLM 排序 + 写推荐理由 → 5条推荐
  5. 写入 clinical_decisions (action_type=education_push, status=pending)
  │
  ▼
response_agent: 生成推荐卡片
  │
  ▼
用户: "确认推送第1、2条"
  │
  ▼
Agent 调用 confirm_education_push(decision_id, selected_ids=[E001,E002])
  → E001, E002: status=executed, 调宣教系统 POST /education/push
  → 其余保持 pending
  → 整体 status=partial
  │
  ▼
Agent: "已推送 2 条，还有 3 条待处理。需要时可追加。"
```

#### 4.1.3 宣教系统接口

| API | 用途 | 优先级 |
|---|---|---|
| `GET /education/search?keyword=xxx&department=xxx&top_k=10` | 关键词匹配宣教内容 | **P0** |
| `POST /education/push` | 执行推送 | **P0** |

### 4.2 场景二：输液滴速调整（`action_type = infusion_adjust`）

#### 4.2.1 数据结构

```json
// action_params
{
  "device_id": "INF-03-001",
  "current_rate": 60,          // 当前滴速（滴/分钟）
  "suggested_rate": 40,        // AI 建议滴速
  "reason": "心率从75升至120，建议降低滴速以减轻心脏负荷",
  "safety_level": "medium",    // low | medium | high
  "vital_signs": {             // 触发时的体征数据
    "heart_rate": 120,
    "blood_pressure": "150/95",
    "spo2": 97
  },
  "items": [
    {"action": "adjust_rate", "from": 60, "to": 40, "status": "pending"}
  ]
}
```

#### 4.2.2 自然语言触发

```
用户: "3床心率快了，把滴速从60调到40"
  │
  ▼
supervisor → intent: infusion_adjust
  │
  ▼
infusion_agent:
  1. LLM 解析: patient=3床, from=60, to=40, trigger=心率快
  2. safety_check:
     - 40 是否在该药品的安全滴速范围内？
     - 调幅 33%，是否需要二次确认？
     → safety_level = medium
  3. 写入 clinical_decisions (action_type=infusion_adjust, status=pending)
  │
  ▼
response_agent:
  "建议将3床滴速从 60 调至 40，原因：... [确认调整] [取消]"
  │
  ▼
用户确认 → Agent 调输液泵 API → status=resolved
```

#### 4.2.3 体征告警自动触发

```
监护仪: 3床心率 ↑120（超过阈值110）
  │
  ▼
POST /api/v1/clinical-events/vital-alert
{
  "event_type": "vital_alert",
  "patient_id": "P20240003",
  "vital_signs": {"heart_rate": 120, "spo2": 97, "infusion_rate": 60},
  "device_id": "INF-03-001"
}
  │
  ▼
infusion_agent:
  1. LLM 分析: 心率升高 + 当前滴速偏高 → 建议降至40
  2. 写入 clinical_decisions
  3. 有活跃对话 → 注入系统消息 + 确认卡片
  4. 无活跃对话 → 落库 + 推送通知
```

#### 4.2.4 输液泵系统接口

| API | 用途 | 优先级 |
|---|---|---|
| `GET /infusion/device/{device_id}/status` | 查询当前滴速和设备状态 | P1 |
| `POST /infusion/device/{device_id}/rate` | 调整滴速 | **P0** |
| `GET /infusion/drug/{drug_id}/safe-range` | 查询药品安全滴速范围（供 safety_check） | P1 |

### 4.3 安全分级

不同的 `action_type` 有不同的执行风险，确认机制分级处理：

| 安全等级 | 典型场景 | 确认方式 | 回滚能力 |
|---|---|---|---|
| **low** | 宣教推送 | 对话中确认即可 | 无需回滚 |
| **medium** | 输液滴速小幅调整（±30%以内） | 对话确认 + 显示调整前后对比 | 可回调 |
| **high** | 输液滴速大幅调整、停药 | 对话确认 + 二次确认 + 强制记录操作者 | 可紧急停止 |

#### 权限模型

现有系统已有 JWT 认证 + workspace 隔离，临床决策需要在此基础上增加基于角色的**执行权限分级**。

| 角色 | 权限 |
|---|---|
| **护士** | 查看决策、确认 low 级操作（宣教推送） |
| **高级护士** | 以上 + 确认 medium 级操作（输液小幅调整） |
| **医生** | 以上 + 确认 high 级操作（输液大幅调整、停药）+ 跳过推荐 |
| **管理员** | 查看所有决策记录，不可执行临床操作 |

**safety_level 与角色对应**：

```
safety_level: low      → 护士及以上可确认
safety_level: medium   → 高级护士及以上可确认
safety_level: high     → 医生可确认
```

**workspace 隔离**：用户只能看到自己 workspace 内的患者决策，维持现有 `_context_store` 的隔离逻辑。

**审计追溯**：`confirmed_by` 记录操作者 user_id，`last_confirmed_at` 记录时间，所有操作可追溯。

**确认 API 的权限校验**（现有 `get_current_user` 扩展）：

```python
# dependencies.py 新增
async def require_clinical_role(min_role: str = "nurse"):
    """校验当前用户是否有指定临床角色"""
    user = await get_current_user(...)
    role = user.get("clinical_role", "nurse")
    role_levels = {"nurse": 1, "senior_nurse": 2, "doctor": 3}
    if role_levels.get(role, 0) < role_levels.get(min_role, 0):
        raise HTTPException(403, "权限不足")
    return user

# 使用
@router.post("/clinical/decisions/{id}/confirm")
async def confirm_decision(
    id: str,
    data: ConfirmRequest,
    user = Depends(require_clinical_role)
):
    decision = await get_decision(id)
    min_role = {"low": "nurse", "medium": "senior_nurse", "high": "doctor"}
    require_clinical_role(min_role[decision.safety_level])
    ...
```

#### 需要业务系统配合

用户体系的 `clinical_role` 字段可以由业务系统在登录时传入，或者 AI Agent 从业务系统同步：

| 配合项 | 说明 |
|---|---|
| 用户角色同步 | 登录时返回 `clinical_role`（nurse / senior_nurse / doctor） |
| 角色变更通知 | 人员调动时同步更新角色 |

### 4.4 决策进化机制

每一次确认/跳过都是天然的标注数据。`clinical_decisions` 表积累的"诊断 → 推荐 → 人怎么选的"就是进化燃料。

#### 4.4.1 反馈信号

| 信号 | 来源 | 含义 |
|---|---|---|
| ✅ **正样本** | 推荐被确认执行 | 这条匹配是对的 |
| ❌ **负样本** | 推荐被跳过 | 这条匹配可能不对 |
| ⚠️ **缺失** | 医生手动搜索了推荐列表之外的内容 | 该推荐但没有推荐 |

#### 4.4.2 进化路径（按复杂度递进）

```
Phase 1-2          Phase 2-3            Phase 4+              Phase 5+
─────────          ─────────            ─────────             ─────────
Few-shot  ──→  跳过反馈  ──→  相似病例  ──→  微调小模型
注入             权重调整        复用               专用决策模型
```

#### Level 1：Few-shot 注入（即时见效）

把历史上"诊断 → 确认了哪些宣教"的成功案例，作为示例注入到 `education_agent` 的 prompt 中：

```python
# education_agent 分析时
async def analyze(self, state):
    # 1. 从 decision 历史中检索相似诊断
    similar = await search_similar_decisions(
        action_type="education_push",
        trigger_text=state["user_input"],
        status="resolved",              # 只取已完成的
        min_confirmed_ratio=0.5,         # 确认率 ≥ 50%
        limit=3
    )

    # 2. 注入到 LLM prompt 作为参考
    few_shot_examples = format_few_shot(similar)
    # "历史上类似诊断'2型糖尿病，HbA1c 8.0%'时，
    #  医生确认推送了：糖尿病饮食指南、血糖监测方法..."

    prompt = f"""{few_shot_examples}

    当前诊断：{state["user_input"]}
    请参考历史决策，推荐宣教内容。"""
```

**效果**：用的越多，cold start 越短。新来的"2型糖尿病"诊断直接复用历史上类似病例的推荐结果。

#### Level 2：跳过反馈权重

统计每个"诊断关键词 → 宣教内容"组合的历史表现，在排序时加权：

```python
# 每次决策完成后更新统计
async def update_feedback_stats(decision: ClinicalDecision):
    for item in decision.action_params["items"]:
        keywords = extract_keywords(decision.trigger_text)
        for kw in keywords:
            if item["status"] == "executed":
                feedback_store.incr(kw, item["id"], score=+1)
            elif item["status"] == "skipped":
                feedback_store.incr(kw, item["id"], score=-1)

# 排序时融合历史反馈
def rank_with_feedback(items, keywords):
    for item in items:
        feedback_score = feedback_store.get_avg(keywords, item["id"])
        item["final_score"] = item["match_score"] * 0.7 + feedback_score * 0.3
    return sorted(items, key=lambda x: x["final_score"], reverse=True)
```

**效果**：被跳过 3 次的"降糖药使用注意事项"在"2型糖尿病"场景下自动降权，不再反复推荐。

#### Level 3：相似病例直接复用

对新的临床事件，先在历史决策中找"几乎相同"的病例。如果相似度 > 阈值，直接复用推荐结果 + 微调建议理由，跳过 LLM 全文分析的步骤：

```
新诊断 → embedding → 检索历史 decisions → 相似度 0.93？
  → 是 → 复用历史推荐的宣教列表 + LLM 只写推荐理由
  → 否 → 走完整 LLM 分析流程
```

#### Level 4：微调决策模型

积累到一定量（比如 5000+ 条已完成决策）后，用这些数据微调一个小模型。输入诊断文本，直接输出推荐宣教列表，不需要 RAG → LLM 多步链。

#### 4.4.3 实现起点

Phase 1 就可以埋点——`clinical_decisions` 表设计里 `action_params.items[].status` 天然记录了反馈。进化机制可以在积累一定数据后（比如 200+ 条决策）再接入，不影响 Phase 1 交付。

---

## 5. API 设计

### 5.1 通用临床事件入口

```
POST /api/v1/clinical-events
Authorization: Bearer <token>

Request:
{
  "event_type": "diagnosis_created",    // diagnosis_created | vital_alert | lab_result | prescription_new
  "patient_id": "P20240001",
  "patient_name": "张三",
  "payload": {                          // event_type 特定的数据
    "diagnosis": "2型糖尿病，HbA1c 8.5%",
    "department": "内分泌科"
  },
  "conversation_id": "conv-xxx",        // 可选，关联已有AI对话
  "metadata": {}
}

Response 202:
{
  "decision_id": "dec-uuid-xxx",
  "action_type": "education_push",
  "status": "pending",
  "injected_to_conversation": true
}
```

### 5.2 决策查询

```
GET /api/v1/clinical/decisions
  ?action_type=education_push
  &status=pending,partial
  &patient_id=P20240001

Response:
{
  "items": [
    {
      "id": "dec-uuid-xxx",
      "action_type": "education_push",
      "patient_name": "张三",
      "trigger_text": "2型糖尿病...",
      "action_params": {...},
      "status": "pending",
      "created_at": "2026-07-15T10:30:00Z"
    }
  ]
}
```

### 5.3 确认执行（通用）

```
POST /api/v1/clinical/decisions/{decision_id}/confirm

Request（通用字段 + action_type 特定字段）:
{
  "selected_ids": ["E001", "E002"],     // 场景特定的选中项
  "comment": "已确认"                    // 可选备注
}

处理逻辑（通用）:
  1. 校验 decision 状态（只有 pending/partial 可操作）
  2. 逐条处理 selected_ids → 调用对应业务系统 API
  3. 未被选中的条目 → 保持 pending
  4. 聚合 status: pending | partial | resolved

Response:
{
  "decision_id": "dec-uuid-xxx",
  "action_type": "education_push",
  "status": "partial",
  "executed": [
    {"id": "E001", "title": "糖尿病饮食指南", "status": "success"}
  ],
  "failed": [],
  "remaining": [
    {"id": "E003", "title": "降糖药使用注意事项", "status": "pending"}
  ],
  "summary": "已执行 1 条，还有 2 条待处理"
}
```

### 5.4 跳过

```
POST /api/v1/clinical/decisions/{decision_id}/skip

Request:
{
  "skip_ids": ["E003"]
}

Response:
{
  "decision_id": "dec-uuid-xxx",
  "status": "partial",
  "skipped": ["E003"],
  "remaining": [...]
}
```

### 5.5 取消

```
POST /api/v1/clinical/decisions/{decision_id}/cancel
// 全部未执行的条目标记为 cancelled，已执行的不可撤回
```

---

## 6. Workflow 设计

### 6.1 扩展后的 Graph

```python
# graph/workflow.py

WORKFLOW_NODES = frozenset({
    "supervisor", "kb_agent", "calc_agent", "response_agent",
    "education_agent",   # 🆕
    "infusion_agent",    # 🆕
})

def build_workflow():
    graph = StateGraph(WorkflowState)

    # 现有节点
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("kb_agent", kb_agent_node)
    graph.add_node("calc_agent", calc_agent_node)
    graph.add_node("response_agent", response_agent_node)

    # 🆕 场景 agent
    graph.add_node("education_agent", education_agent_node)
    graph.add_node("infusion_agent", infusion_agent_node)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges("supervisor", route_intent, {
        "knowledge_query": "kb_agent",
        "calculation": "calc_agent",
        "general": "response_agent",
        "education_recommend": "education_agent",   # 🆕
        "infusion_adjust": "infusion_agent",         # 🆕
    })

    graph.add_edge("kb_agent", "response_agent")
    graph.add_edge("calc_agent", "response_agent")
    graph.add_edge("education_agent", "response_agent")
    graph.add_edge("infusion_agent", "response_agent")
    graph.add_edge("response_agent", END)

    return graph.compile()
```

### 6.2 场景 Agent 基类

```python
# graph/agents/base.py

class ClinicalAgent:
    """临床场景 agent 基类"""

    action_type: str                          # 子类定义

    async def analyze(self, state: WorkflowState) -> dict:
        """LLM 分析提取 → 子类实现具体逻辑"""
        raise NotImplementedError

    async def execute(self, decision_id: str, selected_ids: list[str]) -> dict:
        """执行确认的操作 → 子类实现对业务系统的调用"""
        raise NotImplementedError

    def safety_check(self, params: dict) -> dict:
        """安全检查，返回 {"level": "low|medium|high", "warnings": [...]}"""
        return {"level": "low", "warnings": []}

    async def run(self, state: WorkflowState) -> dict:
        """完整流程"""
        extracted = await self.analyze(state)
        safety = self.safety_check(extracted)
        decision_id = await self.persist(state, extracted, safety)
        return {"agent_result": self.format_response(extracted, decision_id), ...}
```

### 6.3 WorkflowState 扩展

```python
class WorkflowState(TypedDict):
    # 现有字段
    user_input: str
    chat_history: list[BaseMessage]
    user_id: str
    workspace: str
    intent: str
    agent_result: str
    kb_hit: bool
    sources: list[dict]
    intermediate_steps: Annotated[list[Any], merge_steps]
    final_output: str

    # 🆕 临床决策字段
    action_type: str        # education_push | infusion_adjust
    decision_id: str        # clinical_decisions 表 ID
    safety_level: str       # low | medium | high
```

---

## 7. Agent Tool 注册

```python
# 每个场景注册自己的 Tool，Agent 在对话中可调用

# ──── 宣教场景 ────
@tool
async def recommend_education(diagnosis_text: str, patient_id: str) -> str:
    """分析诊断，匹配宣教内容，返回推荐列表。"""
    ...

@tool
async def confirm_education_push(decision_id: str, selected_ids: list[str]) -> str:
    """确认推送宣教，支持部分选择，未选的保留待处理。"""
    ...

# ──── 输液场景 ────
@tool
async def query_infusion_status(patient_id: str) -> str:
    """查询患者当前输液滴速和设备状态。"""
    ...

@tool
async def adjust_infusion_rate(
    patient_id: str,
    device_id: str,
    target_rate: int,
    reason: str
) -> str:
    """调整输液滴速，AI 会先做安全检查再建议确认。"""
    ...
```

---

## 8. 前端配合

### 8.1 WebSocket 消息类型

```json
// AI 返回临床决策推荐（通用消息类型）
{
  "type": "clinical_decision",
  "data": {
    "decision_id": "dec-uuid-xxx",
    "action_type": "education_push",
    "patient_name": "张三",
    "safety_level": "low",
    "summary": "根据诊断推荐 5 条宣教内容",
    "items": [
      {
        "id": "E001",
        "title": "糖尿病饮食指南",
        "subtitle": "匹配度 95%",
        "detail": "HbA1c 8.5% 急需饮食干预",
        "status": "pending"
      }
    ]
  }
}
```

### 8.2 前端渲染

前端根据 `action_type` 选择卡片样式，根据 `safety_level` 决定确认强度：

| safety_level | 确认交互 |
|---|---|
| low | 勾选 + [确认推送] |
| medium | 勾选 + 显示变更前后对比 + [确认调整] |
| high | 勾选 + 二次确认弹窗 + 强制填写操作者 + [确认执行] |

### 8.3 卡片状态复用

同一 `decision_id` 再次打开时，前端根据每个 item 的 `status` 分区展示：

```
┌─────────────────────────────────────────────┐
│  📋 宣教推荐 · 患者：张三                      │
│                                              │
│  已处理：                                     │
│  ✅ 糖尿病饮食指南            已推送 7/15      │
│                                              │
│  待处理：                                     │
│  ☑ 血糖自我监测方法         88%  [推送]        │
│  ☐ 降糖药使用注意事项       82%  [跳过]        │
│                                              │
│  [追加推送]   [全部完成，关闭]                  │
└─────────────────────────────────────────────┘
```

---

## 9. 关键设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 场景隔离 | 每个 action_type 独立 agent + 独立 Tool | 安全等级不同，代码隔离防止互相污染 |
| 决策表 | 一张 `clinical_decisions` 通用表 | `action_type` + `action_params` JSONB 足够灵活 |
| 状态模型 | 每条 item 独立 status，整体聚合 | 部分确认是常态 |
| 安全分级 | safety_level 控制确认强度 | low 只需对话确认，high 需要二次确认 |
| 权限控制 | safety_level 与角色绑定 | 护士推宣教，高级护士调滴速，医生做高风险操作 |
| 审计追溯 | `confirmed_by` 记录操作者，所有操作可追溯 | 医疗安全合规要求；AI 记录决策过程，业务系统记录执行结果 |
| 执行归属 | 业务系统执行，Agent 只触发 | AI 不直接控制医疗设备 |
| 进化机制 | 用确认/跳过反馈逐步优化推荐，分 4 级递进 | Phase 1 先埋点，积累 200+ 条后接入 Level 1 few-shot |
| 触发方式 | 双通道：Webhook + 对话 | 系统自动 + 人工主动 |

---

## 10. 实施路线图

### Phase 1：宣教推送 + 通用框架（2-3 天）

- [ ] 新建 `clinical_decisions` 通用表 + migration
- [ ] 实现场景 agent 基类 `graph/agents/base.py`
- [ ] 实现宣教场景 `education_agent` + supervisor 路由
- [ ] 对接宣教系统 `GET /education/search` + `POST /education/push`
- [ ] 通用决策 API：`POST /clinical/decisions/{id}/confirm`、`skip`、`cancel`
- [ ] 注册宣教 Tool + 对话内端到端验证（全量确认 + 部分确认）

### Phase 2：前端体验（2-3 天）

- [ ] WebSocket 新增 `clinical_decision` 消息类型
- [ ] 确认卡片组件（根据 `action_type` + `safety_level` 自适应）
- [ ] 累计状态展示（已处理 / 待处理分区）
- [ ] 管理端待处理决策列表
- [ ] 超时自动过期

### Phase 3：HIS 对接 + 输液场景 + 权限（3-5 天）

- [ ] `POST /clinical-events` 统一事件入口
- [ ] HIS 诊断事件 → education_agent 自动触发
- [ ] 体征告警事件 → infusion_agent 自动触发
- [ ] 实现输液场景 `infusion_agent`
- [ ] 对接输液泵系统 API
- [ ] 输液场景安全分级（safety_level = medium/high）
- [ ] `require_clinical_role` 权限依赖注入
- [ ] 角色来源对接（用户系统返回 clinical_role 字段）

### Phase 4：决策进化（2-3 天）

- [ ] 反馈统计表（keyword × content 的确认/跳过计数）
- [ ] Level 1: Few-shot 注入——相似历史决策作为 prompt 参考
- [ ] Level 2: 跳过反馈权重——被多次跳过的内容自动降权

### Phase 5：扩展增强（待定）

- [ ] 用药提醒场景
- [ ] 患者画像（偏好、历史 → 个性化推荐）
- [ ] 宣教效果追踪
- [ ] 多轮对话确认

---

## 11. 附录

### 11.1 宣教文件多格式入库（兜底方案）

> **仅在宣教系统没有搜索 API 时启用。** 正常路径是直接调宣教系统 API。

| 格式 | 提取方式 | 工具 |
|---|---|---|
| PDF/DOCX/TXT | 文本提取 | PyPDFLoader / Docx2txtLoader（已有） |
| PPT | 幻灯片文本 | python-pptx |
| 图片(有文字) | OCR | PaddleOCR |
| 音频/视频 | ASR 语音转文字 | faster-whisper / DashScope |

### 11.2 环境依赖

| 能力 | 推荐方案 | 备注 |
|---|---|---|
| ASR | DashScope API（已有 Key） | 本地部署 faster-whisper 需要 GPU 6GB+ |
| OCR | PaddleOCR | `pip install paddleocr` |
| LLM | 现成大模型服务（OpenAI 兼容） | 零代码切换 |
