from langchain_classic.agents import AgentExecutor
from starlette.websockets import WebSocket

from app.llm.callbacks import StreamingCallbackHandler
from app.llm.llm_logger import get_llm_callbacks


def _extract_chunk_text(chunk) -> str:
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content) if content else ""


def _extract_message_text(output) -> str:
    if output is None:
        return ""
    content = getattr(output, "content", output)
    return _extract_chunk_text(content)


async def stream_agent_to_websocket(
    websocket: WebSocket,
    agent: AgentExecutor,
    agent_input: dict,
) -> dict:
    """通过 astream_events 将 Agent 响应流式推送到 WebSocket"""
    callback = StreamingCallbackHandler(websocket)
    streamed_text: list[str] = []
    final_output = ""
    intermediate_steps = []

    async for event in agent.astream_events(
        agent_input,
        version="v2",
        config={"callbacks": get_llm_callbacks([callback])},
    ):
        event_type = event.get("event")
        if event_type == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            token = _extract_chunk_text(chunk)
            if token:
                streamed_text.append(token)
                await websocket.send_json({"type": "token", "data": token})
        elif event_type == "on_chat_model_end":
            text = _extract_message_text(event["data"].get("output"))
            if text and not streamed_text:
                streamed_text.append(text)
                await websocket.send_json({"type": "token", "data": text})
        elif event_type == "on_chain_end" and event.get("name") == "AgentExecutor":
            output = event["data"].get("output")
            if isinstance(output, dict):
                final_output = output.get("output") or final_output
                intermediate_steps = output.get("intermediate_steps", intermediate_steps)

    if not final_output:
        final_output = "".join(streamed_text)

    return {"output": final_output, "intermediate_steps": intermediate_steps}
