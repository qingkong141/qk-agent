# Agent 单独发布

只发布 Agent 后端，适配 Docker Engine 20.10 / docker-compose 1.29.2。
平台地址和 Agent 专用配置统一从服务器已有 `/opt/general.env` 读取。
不再需要 `agent.env`、`compose.env`、`general.hy.env` 或 `deploy.sh`。
不包含前端、Ollama 服务、业务数据库和旧测试数据。

## 打包

在仓库根目录运行：

```powershell
backend/venv/Scripts/python.exe scripts/package_server.py --env-file data/server249-release/agent.env
```

`--env-file` 指向配置来源，可以使用旧 Agent env，也可以使用已配置 `AGENT_*` 的全局 env。
该文件只用于提取模型和加密配置，不会完整复制到发布包或写入镜像。
输出目录为 `data/agent-release/`，只有三个文件：

| 文件 | 用途 |
| --- | --- |
| `agent-image.tar` | Agent 镜像，导入后可移走 |
| `compose.agent.yml` | 仅包含 agent 服务的启动配置 |
| `global-env.append.txt` | 首次部署时加入全局 env 的配置片段，含密钥，配置完成后可移走 |

通过内部安全渠道传输发布包。配置片段是一次性配置材料，不是运行时依赖。
重新打包会更新这三个文件，不会删除业务数据或旧发布包。

## 全局配置

保留 `/opt/general.env` 中已有的 `ORGAIOT_SSO_ADDR`、`ORGAIOT_DEVICE_ADDR`、`ORGAIOT_PM_ADDR`、`ORGAIOT_PE_ADDR`。
249/254 各自使用本机的服务地址，不从另一环境覆盖全局文件。
`ORGAIOT_SSO_API_PREFIX` 缺省为 `/api`。

把配置片段中的 `AGENT_*` 项添加或更新到 `/opt/general.env`，不要重复追加同名项。
必须提供 `AGENT_SECRET_KEY`、`AGENT_OPENAI_API_KEY`、`AGENT_OPENAI_API_BASE` 和 `AGENT_LLM_MODEL`。
其他项为保留既有运行参数的可选配置。Compose 只将声明的变量传入 Agent，不会传入全局文件中其他服务的密码。
包含 `$`、`#` 等字符的值保留片段中的单引号，不能对全局 env 执行 shell `source`。

如果携带已有数据库，`AGENT_SECRET_KEY` 必须沿用该数据库原有的 `SECRET_KEY`，否则已保存的凭据无法解密。
不要用其他部署的配置片段覆盖已有业务数据库的加密密钥。

## 启动

上传 `agent-image.tar` 和 `compose.agent.yml` 到 `/opt/agent/`，完成全局配置后执行：

```bash
cd /opt/agent
docker load -i agent-image.tar
chmod 600 /opt/general.env

# 首次创建目录时设置镜像运行用户的权限；已有目录应保留数据。
mkdir -p /opt/agent/runtime
chown 100:101 /opt/agent/runtime

docker-compose --env-file /opt/general.env -f /opt/agent/compose.agent.yml config --quiet
docker-compose --env-file /opt/general.env -f /opt/agent/compose.agent.yml up -d --no-build agent

docker ps --filter name=aiot-agent
curl -f http://127.0.0.1:18731/health
```

上述命令适用于尚无 Agent 容器的新部署。已有数据目录的内部文件也须允许容器用户 100:101 读写。
Agent 监听 `18731:8000`，运行数据挂载 `/opt/agent/runtime:/app/data`，后续更新保留该目录。
每条命令成功后再执行下一条。

### 原有合并部署的更新

如果 249 的 `aiot-agent` 原来由平台 Compose 与覆盖文件一起启动，应保持原来的项目名、配置文件顺序，避免新项目与同名容器冲突。
导入新镜像、更新全局配置和本文件后，继续使用原来的启动方式，只更新 agent：

```bash
docker-compose --env-file /opt/general.env \
  -f /opt/aiot/docker-compose.yml -f /opt/agent/compose.agent.yml \
  up -d --no-build --no-deps agent
```

如果原来指定过 `-p`，仍使用同一个项目名。不要执行整个平台的 down 或 remove-orphans。
本次 Compose 不含 ui，不会更新前端；前端 `/agent-api/` 继续代理到本机 Agent 的 18731 端口。

## 在线模型与验收

默认只使用华为云的 OpenAI 兼容接口。发布配置不含 Ollama 地址和本地模型，不会启动或探测 Ollama。
`/health` 应返回 `status: ok` 和 `llm_provider: openai`，不会包含 Ollama 连接失败信息。
健康检查不代表模型调用成功，还需登录前端验证一次流式对话、设备查询和知识查询。

工作台设备助手的操作知识/MCP 知识查询不依赖向量服务。
旧版上传文档的向量索引与检索已显式关闭（`EMBEDDING_PROVIDER=disabled`），不会将在线对话模型误用为 Embedding 模型。
需要启用该功能时，须另配真实可用的 Embedding 服务、修改 Compose 的对应配置，并重新建立文档索引。

跨 249/254 搬迁业务数据还需迁移账号归属，重新核对已保存连接地址、设备绑定和发布链接。
SQLite 数据应停写后复制或使用 backup 导出；本发布包不携带也不覆盖数据库。
