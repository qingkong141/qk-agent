from starlette.websockets import WebSocket

from app.graph.nodes import NODE_TASKS
from app.graph.workflow import WORKFLOW_NODES
from app.llm.callbacks import StreamingCallbackHandler
from app.llm.llm_logger import get_llm_callbacks
from app.llm.streaming import _extract_chunk_text, _extract_message_text
from app.rag.pipeline import parse_knowledge_sources


async def _push_text(websocket: WebSocket, streamed_text: list[str], text: str) -> None:
    """Ollama 等模型可能不逐 token 流式，一次性推送完整回复"""
    if not text:
        return
    streamed_text.append(text)
    await websocket.send_json({"type": "token", "data": text})


async def _emit_kb_sources(websocket: WebSocket, kb_text: str, *, kb_hit: bool) -> None:
    if not kb_hit:
        return
    sources = parse_knowledge_sources(kb_text)
    if not sources:
        return
    await websocket.send_json({
        "type": "tool_result",
        "data": {
            "tool": "search_knowledge_base",
            "result": kb_text[:500],
            "sources": sources,
        },
    })


async def stream_workflow_to_websocket(
    websocket: WebSocket,
    workflow,
    initial_state: dict,
) -> dict:
    callback = StreamingCallbackHandler(websocket)
    streamed_text: list[str] = []
    final_output = ""
    intermediate_steps: list = []
    sources: list[dict] = []
    in_response_agent = False

    async for event in workflow.astream_events(
        initial_state,
        version="v2",
        config={"callbacks": get_llm_callbacks([callback])},
    ):
        event_type = event.get("event")
        name = event.get("name", "")

        if event_type == "on_chain_start" and name in WORKFLOW_NODES:
            if name == "response_agent":
                in_response_agent = True
            await websocket.send_json({
                "type": "agent_start",
                "data": {"agent": name, "task": NODE_TASKS.get(name, "")},
            })
        elif event_type == "on_chain_end" and name == "kb_agent":
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                kb_text = output.get("agent_result", "")
                kb_hit = output.get("kb_hit", False)
                steps = output.get("intermediate_steps", [])
                if steps:
                    intermediate_steps.extend(steps)
                if output.get("sources"):
                    sources = output["sources"]
                elif kb_hit:
                    parsed = parse_knowledge_sources(kb_text)
                    if parsed:
                        sources = parsed
                await _emit_kb_sources(websocket, kb_text, kb_hit=kb_hit)
            await websocket.send_json({
                "type": "agent_end",
                "data": {"agent": name, "summary": "完成"},
            })
        elif event_type == "on_chain_end" and name == "response_agent":
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                text = output.get("final_output", "")
                if text:
                    final_output = text
                    if not streamed_text:
                        await _push_text(websocket, streamed_text, text)
                steps = output.get("intermediate_steps", [])
                kb_hit = output.get("kb_hit", False)
                if steps:
                    intermediate_steps.extend(steps)
                    if kb_hit:
                        for step in steps:
                            tool = step[0].tool if hasattr(step[0], "tool") else ""
                            if tool == "search_knowledge_base":
                                await _emit_kb_sources(websocket, str(step[1]), kb_hit=True)
                                break
            in_response_agent = False
            await websocket.send_json({
                "type": "agent_end",
                "data": {"agent": name, "summary": "完成"},
            })
        elif event_type == "on_chain_end" and name in WORKFLOW_NODES:
            await websocket.send_json({
                "type": "agent_end",
                "data": {"agent": name, "summary": "完成"},
            })
        elif event_type == "on_chat_model_stream" and in_response_agent:
            token = _extract_chunk_text(event["data"]["chunk"])
            if token:
                streamed_text.append(token)
                await websocket.send_json({"type": "token", "data": token})
        elif event_type == "on_chat_model_end" and in_response_agent:
            text = _extract_message_text(event["data"].get("output"))
            if text and not streamed_text:
                final_output = text
                await _push_text(websocket, streamed_text, text)
        elif event_type == "on_chain_end" and name == "LangGraph":
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                final_output = output.get("final_output") or final_output
                steps = output.get("intermediate_steps", [])
                if steps:
                    intermediate_steps = steps
                if output.get("sources"):
                    sources = output["sources"]

    if not final_output:
        final_output = "".join(streamed_text)

    return {"output": final_output, "intermediate_steps": intermediate_steps, "sources": sources}
