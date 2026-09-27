"""Connection examples; catalog availability is not a vendor connectivity result."""
TEMPLATES = [
    {'id':'deepl', 'name':'DeepL 文本处理', 'category':'nlp', 'transport':'stdio', 'stdio_profile':'deepl', 'url':'', 'auth_type':'env',
     'description':'文本翻译、文档翻译与文字改写', 'requirement':'后台安装并登记 DeepL 本地服务，填写 DeepL API Key。',
     'docs':'https://github.com/DeepL/deepl-mcp-server'},
    {'id':'paddleocr', 'name':'PaddleOCR 图像识别', 'category':'vision', 'transport':'streamable_http', 'url':'', 'auth_type':'none',
     'description':'图片文字识别、设备铭牌识别及文档解析', 'requirement':'部署 PaddleOCR MCP 和推理服务，填写部署后的实际端点；认证方式按部署配置。',
     'docs':'https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/integrations/mcp_server.md'},
    {'id':'fal', 'name':'fal 多模态生成', 'category':'multimodal', 'transport':'streamable_http', 'url':'https://mcp.fal.ai/mcp', 'auth_type':'bearer',
     'description':'图片、视频及音频生成', 'requirement':'申请 fal API Key，并确认所选模型的使用额度。',
     'docs':'https://blog.fal.ai/connect-your-ai-to-1-000-models-with-the-fal-mcp-server/'},
    {'id':'amap', 'name':'高德地理位置', 'category':'geo', 'transport':'streamable_http', 'url':'https://mcp.amap.com/mcp', 'auth_type':'query', 'query_name':'key',
     'description':'地点查询、地理编码与路线规划', 'requirement':'申请高德 Web 服务 Key；密钥独立填写，无需拼入地址。',
     'docs':'https://lbs.amap.com/api/mcp-server/gettingstarted'},
]
