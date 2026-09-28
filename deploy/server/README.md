# 接入现有服务器部署

本目录用于用户提供的现有平台 Compose：前端 service 名为 `ui`、容器名 `aiot-ui`、端口 `81:80`，新增 service `agent`、容器名 `aiot-agent`、端口 `18731:8000`。
`compose.agent.yml` 是覆盖文件，只覆盖前端部署参数、增加 Agent；不是整个平台的替代配置。
不修改 Redis、设备管理、系统管理、冷链等服务。

## 已确认与待确认

- 本机镜像是 Linux/amd64，服务器需匹配架构。服务器版本为 Docker Engine 20.10.12、docker-compose 1.29.2，配置已用相同版本 Compose 通过解析验证，无需为了部署配置升级 Docker。
- 使用带连字符的 `docker-compose`。Agent env 使用普通文件列表，不使用 `format: raw`；启动脚本自行等待健康状态，不使用 `--wait`。
- 本次先部署 249，后续另行部署 254；两台服务器各自使用独立 `/opt/agent/runtime`。代码部署本身不要求迁移账号：使用空数据库可正常登录、创建配置。只有携带现有 254 流程样例到 249 时才需要迁移数据归属。
- Agent 平台用户 ID 包含 SSO 地址、令牌 issuer、userID。换环境后须校验目标账号并迁移其流程、模型及连接配置归属，否则登录后看不到原记录。不要删除或绕过该身份隔离逻辑。
- 已保存的数据源地址、设备 ID、地图会话和匿名发布凭据也不会随环境变量自动更新。跨环境应保留原始采集数据的来源说明，并重新绑定目标设备、校验连接、登录后更新发布访问设置。
- 全局 `ORGAIOT_SSO_ADDR` 指向 `249:8087`，另一个旧变量 `VUE_APP_PMUI_SSO` 指向 `249:7009/am/api`。本覆盖文件统一使用 `ORGAIOT_SSO_ADDR`；直连通常配 `/api`，若现场改走网关则同时改为网关地址和 `/am/api`。以现场有效登录入口为准。

## 1. 备份并修正原 Compose

以下示例假定现有目录 `/opt/aiot`、文件名 `docker-compose.yml`；如果实际名称不同，替换命令中的名称。
备份原文件和当前前端镜像，记录 `docker inspect aiot-ui --format '{{.Image}}'` 的结果。
原文件 `ui.build.args` 里的 `ORGAIOT_PM_ADDR`、`ORGAIOT_PE_ADDR` 各出现两次，先删除第二组重复项，否则新版 Compose 会拒绝解析。
保留其余业务服务配置原样。

docker-compose 1.29.2 只支持一个 `--env-file`。启动脚本将 `/opt/general.env` 和 `/opt/aiot/general.hy.env` 按顺序合并到 `/opt/agent/compose.env`，后者覆盖前者同名变量。原文件不修改，合并文件权限为 600。
服务中的 `env_file:` 不会自动为 Compose 的 `${...}` 提供替换值。
不要通过 shell 的 `source` 读取这类包含冒号的 .NET 配置键。

## 2. 准备镜像及清理后的数据

在前端项目 PowerShell 运行，构建本次 249 镜像：

```powershell
$env:VITE_APP_PE_SERVICE='http://192.168.10.249:7039/api'
$env:VITE_APP_PE_MQTT_URL='ws://192.168.10.249:15674/ws'
pnpm build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
docker build -t orgaiot-platform-ui:249-20260928 .
if ($LASTEXITCODE -ne 0) { throw 'Frontend image build failed' }
docker image save --output D:\zhencheng\ai-agent\data\server-images.tar orgaiot-agent:20260928 orgaiot-platform-ui:249-20260928
```

当前本机 `orgaiot-platform-ui:20260928` 中 PE/MQTT 浏览器直连地址仍为 254，不能直接作为 249 版使用。切换环境需按上述方式重建前端。
后续在 254 服务器的全局 env 中加 `AIOT_UI_IMAGE=orgaiot-platform-ui:20260928`，导入对应镜像。Agent 镜像相同，地址继续通过全局 `ORGAIOT_*` 变量控制。

使用清理后的 `data/docker/runtime`，不要重新复制开发环境 `backend/data`。
数据库须通过运行容器内 SQLite backup 导出，或短暂停止本机 Agent 后复制，不能直接拷贝正在写入的 SQLite 文件。
一并带上 `datasets/`、`documents/`、`chroma/`；无需迁移 `backups/` 中已删除测试数据的旧备份和历史日志。
跨 249/254 的账号与数据关联迁移尚未执行；需要在目标环境验证登录账号后再制作最终数据包，不能直接搬过去就认为原流程已可见。

保留 `data/docker/agent.env` 为服务器的 `/opt/agent/agent.env`。发布目录根部已提供该文件，上传到 `/opt/agent/` 即可。其中包含加密所需的原 SECRET_KEY 和 Agent 专属参数，限制文件访问，不提交到 Git。
平台 SSO/设备/PM/PE 地址由覆盖文件中的 `environment` 从全局 env 映射，优先于此文件中的旧地址。
Agent 不需要接收整份全局文件里的其他系统密码，也不使用原业务系统的数据库作为工作台数据库。

## 3. 上传后的目录

```text
/opt/general.env                   # 服务器已有，保留
/opt/aiot/general.hy.env            # 服务器已有，保留
/opt/aiot/docker-compose.yml        # 服务器已有，只修正重复键
/opt/agent/compose.agent.yml        # 本目录提供的覆盖文件
/opt/agent/deploy.sh                # 兼容 Compose 1.29.2 的启动脚本
/opt/agent/compose.env              # 启动时自动合并生成，勿手工维护
/opt/agent/server-images.tar        # 导出的两个镜像
/opt/agent/agent.env                # Agent 专属密钥及运行参数
/opt/agent/runtime/agent.db         # 首次启动生成，或导入最终迁移库
/opt/agent/runtime/datasets/
/opt/agent/runtime/documents/
/opt/agent/runtime/chroma/
/opt/agent/runtime/logs/
```

将 `data/server249-release` 目录中的文件上传到 `/opt/agent/`。已有 Agent 数据时先备份，不能用本机包覆盖服务器运行中的数据库。
后端程序在 Docker 镜像内，不需要额外上传 Python 源代码。`/opt/agent/runtime` 挂载到容器的 `/app/data`。

## 4. 启动

当前交付目录 `data/server249-release` 已含两个镜像、覆盖文件和 Agent 专属 env，**未携带 254 业务数据库**。
可以先按下面命令启动程序，Agent 会创建空工作台库，249 账号可正常登录和创建配置。
如需带入原流程样例，须完成前述账号归属及连接校验后再放入最终迁移库；不要用旧库覆盖已开始使用的新库。

已上传旧版文件的服务器，只需重新上传 `compose.agent.yml`、`deploy.sh` 和本说明。`server-images.tar`、`agent.env` 不变，无需重新上传。

在服务器以 root 执行：

```bash
cd /opt/agent
# 兼容 Windows 上传工具保留的换行符
sed -i 's/\r$//' deploy.sh
bash deploy.sh /opt/aiot/docker-compose.yml
```

最后一个参数是原平台 Compose 文件；如果实际文件名不同，请替换该路径。
脚本校验文件与合并配置，导入镜像，保留旧前端镜像标签，设置 Agent 数据目录权限，先启动 Agent 并等待健康，再更新前端。
Agent 未健康时不会执行前端更新。只更新 `agent` 和 `ui`，使用 `--no-build --no-deps`，不会重建其他业务容器。
原文件 `ui.build.args` 重复键请仍按前面的说明处理。

脚本执行后可手工检查：

```bash
docker-compose --env-file /opt/agent/compose.env \
  -f /opt/aiot/docker-compose.yml -f /opt/agent/compose.agent.yml ps agent ui
curl -f http://127.0.0.1:18731/health
curl -f http://127.0.0.1:81/agent-api/health
```

`ui` 仍使用现有容器名和 81 端口，Compose 会更新该服务。不要启动另一套同名同端口的前端。
服务器需能访问目标平台服务和华为云模型地址；PE/MQTT 的浏览器直连地址也需被访问者电脑访问到。
通过 `http://服务器IP:81` 登录，核对业务应用、智能体和数据模型；再启动平台轮询任务、验证真实报文和流式对话。
从服务器页面重新复制发布链接。新服务器尚未实测，本说明不代表已经远程上线。

## 5. 回退

启动脚本会输出旧前端的备份标签 `aiot-ui:before-agent-时间`。需要回退时，将覆盖文件 `ui.image` 改成该备份标签，执行：

```bash
docker-compose --env-file /opt/agent/compose.env \
  -f /opt/aiot/docker-compose.yml -f /opt/agent/compose.agent.yml \
  up -d --no-build --no-deps ui
```
如果旧前端依赖原有 build 参数而未提供运行时 env，回退时也要使用备份的原部署配置。
不要执行整个平台的 `down`，不要删除 `/opt/agent/runtime`。后续更新只换镜像，数据库继续使用服务器现有目录。

Docker 官方参考：[导出镜像](https://docs.docker.com/reference/cli/docker/image/save/)、[导入镜像](https://docs.docker.com/reference/cli/docker/image/load/)、[Compose 1.29.2 命令定义](https://github.com/docker/compose/blob/1.29.2/compose/cli/main.py)。
