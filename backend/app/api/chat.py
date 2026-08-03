import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger

from app.core.agent_factory import create_agent_executor
from app.core.context import set_request_context
from app.core.prompts import wrap_user_input
from app.llm.llm_logger import get_llm_callbacks
from app.llm.ollama_health import format_llm_error
from app.dependencies import get_current_user
from app.graph.workflow import get_workflow
from app.llm.streaming import stream_agent_to_websocket
from app.llm.workflow_streaming import stream_workflow_to_websocket
from app.memory.manager import memory_manager
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.message_sources import extract_sources_from_steps


def _resolve_message_sources(result: dict) -> list[dict]:
    sources = result.get("sources") or []
    if sources:
        return sources
    return extract_sources_from_steps(result.get("intermediate_steps", []))

router = APIRouter(tags=["chat"])

_context_store: dict[str, dict] = {}


def _build_input_with_context(message: str, workspace: str) -> str:
    ctx = _context_store.get(workspace, {})
    if not ctx:
        return message
    ctx_parts = [f"用户正在 {ctx.get('system', '未知系统')} 中"]
    if page := ctx.get("page"):
        ctx_parts.append(f"当前页面 {page}")
    if patient := ctx.get("patient"):
        ctx_parts.append(f"查看患者 {patient.get('name', '')}({patient.get('bed', '')})")
    if actions := ctx.get("recentActions"):
        ctx_parts.append(f"最近操作：{'、'.join(actions)}")
    return f"[系统上下文] {'，'.join(ctx_parts)}。\n[用户消息] {message}"


def _secure_input(message: str, workspace: str) -> str:
    return wrap_user_input(_build_input_with_context(message, workspace))


async def _run_single_agent(enriched_input: str, chat_history: list, streaming: bool):
    agent = create_agent_executor(streaming=streaming)
    return await agent.ainvoke(
        {"input": enriched_input, "chat_history": chat_history},
        config={"callbacks": get_llm_callbacks()},
    )


async def _run_workflow(
    enriched_input: str,
    chat_history: list,
    user_id: str,
    workspace: str,
    *,
    streaming: bool = False,
    websocket=None,
):
    workflow = get_workflow()
    state = {
        "user_input": enriched_input,
        "chat_history": chat_history,
        "user_id": user_id,
        "workspace": workspace,
        "intent": "",
        "agent_result": "",
        "kb_hit": False,
        "sources": [],
        "intermediate_steps": [],
        "final_output": "",
    }
    if streaming and websocket:
        return await stream_workflow_to_websocket(websocket, workflow, state)

    result = await workflow.ainvoke(state)
    return {
        "output": result.get("final_output", ""),
        "intermediate_steps": result.get("intermediate_steps", []),
        "intent": result.get("intent", ""),
        "sources": result.get("sources", []),
    }


@router.post("/chat", response_model=ChatResponse)
async def chat_send(data: ChatRequest, user=Depends(get_current_user)):
    conversation_id = data.conversation_id or str(uuid.uuid4())
    workspace = user.get("workspace", "default")
    enriched_input = _secure_input(data.message, workspace)
    set_request_context(user["id"], workspace)

    chat_history = await memory_manager.get_messages(conversation_id)

    if data.use_workflow:
        result = await _run_workflow(enriched_input, chat_history, user["id"], workspace)
        output = result["output"]
        steps = [
            {"tool": step[0].tool if hasattr(step[0], "tool") else str(step[0]), "result": str(step[1])[:200]}
            for step in result.get("intermediate_steps", [])
        ]
    else:
        result = await _run_single_agent(enriched_input, chat_history, streaming=False)
        output = result["output"]
        steps = [
            {"tool": step[0].tool, "result": str(step[1])[:200]}
            for step in result.get("intermediate_steps", [])
        ]

    await memory_manager.append(
        conversation_id,
        data.message,
        output,
        sources=_resolve_message_sources(result),
    )
    return ChatResponse(conversation_id=conversation_id, output=output, intermediate_steps=steps)


@router.websocket("/ws/{conversation_id}")
async def chat_websocket(websocket: WebSocket, conversation_id: str):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message", "")
            workspace = data.get("workspace", "default")
            use_workflow = data.get("use_workflow", True)
            user_id = data.get("user_id", "anonymous")
            set_request_context(user_id, workspace)

            enriched_input = _secure_input(message, workspace)
            chat_history = await memory_manager.get_messages(conversation_id)

            if use_workflow:
                result = await _run_workflow(
                    enriched_input,
                    chat_history,
                    user_id,
                    workspace,
                    streaming=True,
                    websocket=websocket,
                )
            else:
                agent = create_agent_executor(streaming=True)
                agent_input = {"input": enriched_input, "chat_history": chat_history}
                result = await stream_agent_to_websocket(websocket, agent, agent_input)

            resolved_sources = _resolve_message_sources(result)

            await memory_manager.append(
                conversation_id,
                message,
                result["output"],
                sources=resolved_sources,
            )

            await websocket.send_json({
                "type": "done",
                "data": {
                    "session_id": conversation_id,
                    "output": result["output"],
                    "sources": resolved_sources,
                    "intermediate_steps": [
                        {"tool": step[0].tool if hasattr(step[0], "tool") else str(step[0]), "result": str(step[1])[:200]}
                        for step in result.get("intermediate_steps", [])
                    ],
                },
            })
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {conversation_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.send_json({
            "type": "error",
            "data": {"code": "INTERNAL", "message": format_llm_error(e)},
        })
