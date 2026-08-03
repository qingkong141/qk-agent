# AI 智能体平台

通用 AI Agent 平台，支持单 Agent 对话、工具调用、RAG 知识库检索，并通过 Embed SDK 嵌入到任意外部系统。

## 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | Python + FastAPI + LangChain |
| Embed SDK | TypeScript + Rollup |
| 管理台 | Vue 3 + Vite + Naive UI |
| 数据库 | PostgreSQL 16 + pgvector |

## 项目结构

```
ai-agent/
├── backend/     # FastAPI 后端 + LangChain Agent
├── sdk/         # @multi-agent/sdk 嵌入 SDK
├── frontend/    # Vue 3 管理台
└── docker-compose.yml
```

## 内网部署（Ollama，无需外网 API）

### 1. 安装并拉取模型

```bash
# 安装 Ollama: https://ollama.com
ollama pull qwen2.5:7b          # 对话
ollama pull nomic-embed-text    # 知识库向量化
```

### 2. 配置 .env

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434   # 内网改为 http://10.x.x.x:11434
LLM_MODEL=qwen2.5:7b

EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
```

### 3. 健康检查

```bash
curl http://localhost:8000/health
# 返回 ollama.reachable / llm_model_ok / embedding_model_ok
```

### 4. 知识库注意

- 切换 Embedding 模型后，需 **删除 `data/chroma/` 并重新上传文档**
- 推荐模型：`qwen2.5:7b`（对话）、`nomic-embed-text`（向量）

---

## 快速开始

### 1. 环境配置

```bash
cp .env.example .env
# 内网默认已配置 ollama；云端请改 LLM_PROVIDER 并填入 API Key
```

### 2. 后端（开发模式）

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API 文档：http://localhost:8000/docs

局域网访问后端：`http://<本机IP>:8000/docs`（需加 `--host 0.0.0.0`）

### 3. Embed SDK

```bash
cd sdk
npm install
npm run build
```

### 4. 管理台

```bash
cd frontend
npm install
npm run dev
```

浏览器本机：http://localhost:3000  
局域网（手机/Android WebView）：http://\<本机IP\>:3000（如 `http://192.168.20.13:3000`）

> `vite.config.ts` 已设置 `server.host: true`，否则默认只监听 localhost，IP 无法访问。修改配置后需重启 `npm run dev`。

### 5. Docker 部署

```bash
docker-compose up -d
```

## SDK 接入示例

```typescript
import { AgentSDK } from '@multi-agent/sdk'

const sdk = new AgentSDK({
  baseUrl: 'http://localhost:8000/api/v1',
  apiKey: 'ma-xxx',
  workspace: 'nursing-ward-3',
})

sdk.chat.stream({ message: '你好' })
  .on('token', (t) => console.log(t))
  .on('done', () => console.log('完成'))
```

## 开发路线图

- **Phase 0**: Embed SDK 脚手架 ✅
- **Phase 1**: MVP 核心对话能力 ✅
- **Phase 2**: RAG 知识库 ✅
- **Phase 3**: LangGraph 多 Agent 编排 ✅
- **Phase 4**: 生产加固 ✅
