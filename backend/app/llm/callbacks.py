from langchain_core.callbacks import AsyncCallbackHandler
from starlette.websockets import WebSocket

from app.rag.pipeline import is_kb_miss, parse_knowledge_sources


class StreamingCallbackHandler(AsyncCallbackHandler):
    """将 LLM 输出的 token 逐字推送到 WebSocket"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self._current_tool: str | None = None

    async def on_tool_start(self, serialized: dict, input_str: str, **kwargs):
        self._current_tool = serialized.get("name", "unknown")
        await self.websocket.send_json({
            "type": "tool_call",
            "data": {
                "tool": self._current_tool,
                "args": input_str,
            },
        })

    async def on_tool_end(self, output: str, **kwargs):
        result_text = str(output)
        data: dict = {
            "tool": self._current_tool,
            "result": result_text[:500],
        }
        if self._current_tool == "search_knowledge_base" and not is_kb_miss(result_text):
            sources = parse_knowledge_sources(result_text)
            if sources:
                data["sources"] = sources
        await self.websocket.send_json({"type": "tool_result", "data": data})
        self._current_tool = None
