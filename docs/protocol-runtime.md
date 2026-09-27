# 协议插件运行时

`/api/v1/studio/plugins`：列表/创建；`/{id}`详情/删除；`/{id}/versions`创建版本；`/start`指定版本启动；`/stop`停止；`/ingest`POST `{raw:string}`；`/messages`最近100条；`/versions/{versionId}/download`ZIP。

每个插件拥有不可变PluginVersion，来源为服务器保存的protocol_debug配置；创建版本与启动均实际执行JSONata，不信任浏览器通过标记。运行进程是Node.js+JSONata2.2.2，由Python异步管道管理，第一条initialize包含服务器快照，ready后注册。活动指针和历史处理版本记录持久化；替换前验证新进程，失败时旧版保持在线。停止与版本修改采用归属检查/expected_revision/单插件锁。

Node在128MB堆内执行，不注册访问文件、网络或设备的JSONata扩展函数。脚本超时1500ms，Python等待5秒，超时或取消后杀死进程以防响应串位。空目标字段保留null，0保持0，检查源类型、目标类型/范围/未知字段/单位换算。

初始化：Node.js22+，在`backend/protocol-runtime`执行`npm ci --ignore-scripts`。运行`uvicorn app.main:app --host 127.0.0.1 --port 8017`，必须单Worker。启动恢复持久化活动版本，恢复失败通过runtime.error显示。生产多副本、MQTT/TCP接入、设备凭据和网络网关需要额外部署契约，不在当前HTTP运行时内。

`scripts/test_plugins.py`真实启动Node进程，验证61→30.5热切换、回退、错误版本隔离、停止/恢复、权限、历史及ZIP解压独立执行。下载包包含JSONata原许可证及依赖；用户要求暂不做前端生产打包，未执行生产构建。
