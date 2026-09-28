# 服务器完整案例初始化

无需重打 Agent 镜像，使用已有容器中的 Python 和依赖。将 `seed_case.py`、`hospital-case.zip` 上传到 `/opt/agent/`。

```bash
docker cp /opt/agent/seed_case.py aiot-agent:/tmp/seed_case.py
docker cp /opt/agent/hospital-case.zip aiot-agent:/tmp/hospital-case.zip
docker exec -it aiot-agent python /tmp/seed_case.py --account admin
```

按提示输入平台账号密码，输入时不显示。使用平台 SSO 验证身份，249/254 各自在当前环境账号下创建数据。
模型管理中的八个华为云连接统一使用容器当前的 `OPENAI_API_BASE` 和 `OPENAI_API_KEY`，即全局 env 的 `AGENT_*` 映射值。
修改全局 env 后先用 Compose up 更新容器，再执行本脚本。同名模型连接更新密钥，不重复添加；未自动验证每一个模型的计费权限。

内容包括六个科室、48台设备档案及工作台设备、各96条近期上报、4,662条ODS、4,608条DWD、DIM/DWS/ADS，六类应用，管道/数据接口、跨表SQL、图表探索、主索引、语义/语法转换、协议插件、实时推送任务、智能体和MCP编排，以及文本/图像/视频文件。

历史案例时间为2026年9月26日，SQL与应用使用该范围；设备助手上报与实时任务另生成初始化时刻前的近期数据。补齐值、阈值和来源保留在案例说明中。
应用发布为需登录访问；智能体和MCP编排创建为可调试配置，不自动产生收费模型对话或对外免登录链接。
协议插件实际在Node.js运行时校验后启动。SQL、图表探索和应用数据绑定通过接口实际校验。导入的批处理管道可在页面重新运行，脚本不伪造管道执行日志。

仅导入本地整理的生成案例，不复制本地账号、密钥、真实254设备报文、匿名链接、历史调试对话或数据源密码。真实业务连接和第三方MCP连接按目标环境单独配置，不能当成案例数据跨环境搬迁。

重复执行会按进度和同名条目复用数据，不清空记录。进度在 `/opt/agent/runtime/seed-progress/`；运行中断后保留此目录重试。请勿同时手工删除或更改正在导入的案例。模型账号配置每次同步；已有业务配置不会被脚本强行覆盖。

导入请求遇到“平台登录校验服务暂不可用”的503会等待2秒、4秒后重试，最多请求3次；持续失败仍停止并保留进度。其他服务端错误、写入请求超时不自动重放，避免重复写入。更新此重试功能只需替换容器中的 `seed_case.py`，无需重建镜像或重新上传案例数据包。

后续维护者重新制作案例包：在本地仓库执行 `backend/venv/Scripts/python.exe scripts/package_server_case.py`。导出只读SQLite一致性快照中的指定案例与文件，不输出数据库、连接凭据或本地账号。
