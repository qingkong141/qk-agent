# 本机 Docker 发布

前端：http://127.0.0.1:81。Agent：http://127.0.0.1:18731（容器内 8000）。
平台业务接口使用 254 环境。前端通过同源 `/agent-api/` 代理访问 Agent，支持流式对话和文件上传。

## 首次发布

在 ai-agent 根目录用现有虚拟环境执行 `backend/venv/Scripts/python.exe scripts/prepare_local_docker.py`。
该命令复制 SQLite、数据集文件和文档，转换 Windows 文档路径，保留解密模型配置所需的 SECRET_KEY。
数据及密钥存放在 Git 忽略的 `data/docker/`，不会放进镜像。已有 Docker 数据库时会拒绝覆盖。
开发环境与 Docker 使用独立数据库，后续数据修改不会自动同步。

在前端 `OrgAIoT.Platform.UI.v3` 目录的 PowerShell 中构建：

```powershell
$env:VITE_APP_PE_SERVICE='http://192.168.10.254:7039/api'
$env:VITE_APP_PE_MQTT_URL='ws://192.168.10.254:15674/ws'
pnpm build
```

在 ai-agent 根目录：

```powershell
docker compose -f compose.local.yml build
docker compose -f compose.local.yml up -d --wait
docker compose -f compose.local.yml ps
```

首次替换前需要将原 `orgaiot-ui` 容器停止并改名保留。不要处理其他项目的容器。
Docker Desktop 启动后两个容器会自动启动。协议插件自动恢复；平台轮询任务重启后需登录并重新启动，推送任务不需要驻留轮询。

## 更新与回退

更新代码时重复前端构建和 compose 的 build/up 命令，**不要重新运行数据初始化脚本**。
更新前备份 `data/docker/`（数据库运行中使用 SQLite backup，或停止 Agent 后复制）。
该目录包含连接密钥，应限制访问并与部署配置一同备份。

回退前端：停止并移除本次 compose 的 frontend 容器，然后将保留的旧容器改名回 `orgaiot-ui` 并启动。
这不会删除 Agent 数据。不要执行清理数据目录或 Docker volume 的命令。

发布的应用、智能体链接应从 `81` 端口页面重新复制；链接中的令牌保留，原 `5187` 地址仍指向开发环境。
局域网访问可将 `127.0.0.1` 换为本机局域网 IP，防火墙需允许相应端口。
