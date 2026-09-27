# PM/PE地图MCP

新增`/api/v1/studio/mcp/maps/`，工具`platform_map(map_id=null, terminal_ids='')`：无地图ID时读取授权结构目录，指定ID时读取该地图终端坐标。现共11个MCP服务，均已调用实际工作台/平台数据；2026-09-27正常254登录后，地图端点实际读取了授权目录和地图4的22条终端位置。

配置PLATFORM_PM_BASE_URL、PLATFORM_PE_BASE_URL，可选PLATFORM_MAP_SYS_TYPE。本机沿用前端现有254:7034/api、254:7039/api。没有硬编码替代地图坐标。

平台请求携带X-Platform-Token和X-Platform-Session-ID；浏览器在智能体请求中使用已有ssoTokenId。后台按原pe-map-sdk调用GetTokenByID和SSO/RefreshToken，验证定位令牌并比对当前用户/issuer，再读取GetUserAllowMaps。空权限拒绝，非授权地图拒绝，只有明确-1才表示全部；查询结构仍传mapIDs。位置查询必选地图，最多返回500条。换票令牌不写数据库、不返回AI；日志过滤sid和ValidateToken中的token。

地图调用失败会进入智能体工具错误信息，不能当作连接成功或正常坐标。`scripts/test_platform_maps.py`使用隔离HTTP传输测试换票/刷新、身份不匹配、空权限、地图权限及坐标接口契约。会话ID允许SSO实际返回的百分号编码，按SDK约定作为不透明值原样交给HTTP查询参数编码；测试覆盖其传输保真及畸形编码拒绝。真实环境验证包含初始化、工具发现、目录与位置两次工具调用；22条为测试时数量，不是固定返回值。
