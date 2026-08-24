import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_factory import load_agent_runtime_config
from app.core.context import set_request_context
from app.core.input_intent import is_resumable_partial
from app.core.prompts import wrap_user_input
from app.core.turn_planner import TurnPlan, TurnResult, plan_turn
from app.db.session import async_session
from app.llm.ollama_health import format_llm_error
from app.dependencies import DbSession, Principal, authenticate_principal, get_current_user
from app.graph.workflow import get_workflow
from app.llm.workflow_streaming import stream_workflow_to_websocket
from app.memory.manager import memory_manager
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.message_sources import extract_sources_from_steps
from app.services.conversation_access import conversation_external_user_id, get_or_create_owned_conversation
from app.services.runtime_state import get_user_context, save_checkpoint


def _resolve_message_sources(result: dict) -> list[dict]:
    sources = result.get("sources") or []
    if sources:
        return sources
    return extract_sources_from_steps(result.get("intermediate_steps", []))


router = APIRouter(tags=["chat"])

async def _build_input_with_context(
    message: str,
    user_id: str,
    external_user_id: str,
    workspace: str,
) -> str:
    async with async_session() as db:
        ctx = await get_user_context(db, user_id, external_user_id, workspace)
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


async def _secure_input(message: str, user_id: str, external_user_id: str, workspace: str) -> str:
    return wrap_user_input(await _build_input_with_context(message, user_id, external_user_id, workspace))


async def _run_workflow(
    enriched_input: str,
    chat_history: list,
    user_id: str,
    workspace: str,
    *,
    turn_plan: TurnPlan,
    agent_prompt: str = "",
    agent_tools: list[str] | None = None,
    streaming: bool = False,
    websocket=None,
    run_state: dict | None = None,
):
    workflow = get_workflow()
    state = {
        "user_input": enriched_input,
        "chat_history": chat_history,
        "user_id": user_id,
        "workspace": workspace,
        "agent_prompt": agent_prompt,
        "agent_tools": agent_tools,
        "turn_plan": turn_plan.model_dump(mode="json"),
        "response_status": "needs_user_input" if turn_plan.needs_user_input else "completed",
        "intent": "",
        "agent_result": "",
        "kb_hit": False,
        "sources": [],
        "intermediate_steps": [],
        "final_output": "",
    }
    if streaming and websocket:
        result = await stream_workflow_to_websocket(websocket, workflow, state, run_state=run_state)
    else:
        graph_result = await workflow.ainvoke(state)
        result = {
            "output": graph_result.get("final_output", ""),
            "intermediate_steps": graph_result.get("intermediate_steps", []),
            "sources": graph_result.get("sources", []),
            "status": graph_result.get("response_status", "completed"),
        }
    return TurnResult(
        output=result.get("output", ""),
        intent=turn_plan.intent,
        status=result.get("status", "completed"),
        sources=result.get("sources", []),
        intermediate_steps=result.get("intermediate_steps", []),
    ).model_dump()


async def _plan_conversation_turn(conversation_id: str, message: str) -> TurnPlan:
    return await plan_turn(message, {
        "interrupted": await memory_manager.get_last_interrupted_turn(conversation_id),
        "needs_user_input": await memory_manager.get_last_needs_user_input_turn(conversation_id),
    })


async def _handle_websocket_chat(
    websocket: WebSocket,
    conversation_id: str,
    data: dict,
    run_state: dict,
    principal: Principal,
    workspace: str,
) -> None:
    message = data.get("effective_message") or data.get("message", "")
    original_message = data.get("message", "")
    agent_id = data.get("agent_id")
    user_id = principal["id"]
    set_request_context(
        user_id, workspace, external_user_id=principal["external_user_id"],
        conversation_id=conversation_id, run_id=run_state["run_id"],
        approval_id=data.get("approval_id", ""),
    )

    enriched_input = await _secure_input(message, user_id, principal["external_user_id"], workspace)
    chat_history = await memory_manager.get_messages(conversation_id)
    turn_plan = TurnPlan.model_validate(data["turn_plan"])

    async with async_session() as db:
        agent_prompt, agent_tools = await load_agent_runtime_config(db, agent_id)
    result = await _run_workflow(
        enriched_input,
        chat_history,
        user_id,
        workspace,
        turn_plan=turn_plan,
        agent_prompt=agent_prompt,
        agent_tools=agent_tools,
        streaming=True,
        websocket=websocket,
        run_state=run_state,
    )

    async with async_session() as db:
        await save_checkpoint(
            db, run_id=run_state["run_id"], conversation_id=conversation_id,
            owner_id=user_id, external_user_id=principal["external_user_id"],
            phase="response", status="completed",
            state={"output": result["output"], "turn_plan": data["turn_plan"]},
        )

    resolved_sources = _resolve_message_sources(result)
    response_status = result.get("status", "completed")

    await memory_manager.append(
        conversation_id,
        original_message,
        result["output"],
        sources=resolved_sources,
        status=response_status,
        run_id=run_state["run_id"],
        metadata={
            "resumed_from": data.get("resumed_from"),
            "clarified_from": data.get("clarified_from"),
            "original_message": original_message,
        },
    )

    await websocket.send_json({
        "type": "done",
        "data": {
            "session_id": conversation_id,
            "output": result["output"],
            "status": response_status,
            "sources": resolved_sources,
            "intermediate_steps": [
                {"tool": step[0].tool if hasattr(step[0], "tool") else str(step[0]), "result": str(step[1])[:200]}
                for step in result.get("intermediate_steps", [])
            ],
        },
    })


@router.post("/chat", response_model=ChatResponse)
async def chat_send(data: ChatRequest, db: DbSession, user=Depends(get_current_user)):
    conversation_id = data.conversation_id or str(uuid.uuid4())
    conversation = await get_or_create_owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=user["id"],
        external_user_id=conversation_external_user_id(user),
        workspace=user.get("workspace", "default"),
    )
    workspace = conversation.workspace
    run_id = str(uuid.uuid4())
    set_request_context(
        user["id"], workspace, external_user_id=user["external_user_id"],
        conversation_id=conversation_id, run_id=run_id, approval_id=data.approval_id or "",
    )

    chat_history = await memory_manager.get_messages(conversation_id)
    turn_plan = await _plan_conversation_turn(conversation_id, data.message)
    enriched_input = await _secure_input(
        turn_plan.effective_message,
        user["id"],
        user["external_user_id"],
        workspace,
    )
    await save_checkpoint(
        db, run_id=run_id, conversation_id=conversation_id, owner_id=user["id"],
        external_user_id=user["external_user_id"], phase="planned", status="running",
        state={"message": data.message, "turn_plan": turn_plan.model_dump(mode="json")},
    )

    agent_prompt, agent_tools = await load_agent_runtime_config(db, data.agent_id)
    result = await _run_workflow(
        enriched_input,
        chat_history,
        user["id"],
        workspace,
        turn_plan=turn_plan,
        agent_prompt=agent_prompt,
        agent_tools=agent_tools,
    )
    output = result["output"]
    steps = [
        {"tool": step[0].tool if hasattr(step[0], "tool") else str(step[0]), "result": str(step[1])[:200]}
        for step in result.get("intermediate_steps", [])
    ]

    await memory_manager.append(
        conversation_id,
        data.message,
        output,
        sources=_resolve_message_sources(result),
        status=result.get("status", "completed"),
    )
    return ChatResponse(
        conversation_id=conversation_id,
        output=output,
        status=result.get("status", "completed"),
        intermediate_steps=steps,
    )
    await save_checkpoint(
        db, run_id=run_id, conversation_id=conversation_id, owner_id=user["id"],
        external_user_id=user["external_user_id"], phase="response",
        status=result.get("status", "completed"), state={"output": output},
    )


@router.websocket("/ws/{conversation_id}")
async def chat_websocket(websocket: WebSocket, conversation_id: str):
    await websocket.accept()
    current_task: asyncio.Task | None = None

    try:
        data = await websocket.receive_json()
        if data.get("type", "chat") != "chat":
            await websocket.send_json({
                "type": "error",
                "data": {"code": "INVALID_MESSAGE", "message": "请先发送 chat 消息"},
            })
            return

        authorization = websocket.headers.get("authorization", "")
        header_token = authorization[7:] if authorization.lower().startswith("bearer ") else None
        bearer_token = header_token or data.pop("access_token", None)
        api_key = websocket.headers.get("x-api-key") or data.pop("api_key", None)
        async with async_session() as db:
            principal = await authenticate_principal(
                db,
                bearer_token=bearer_token,
                api_key=api_key,
                end_user_id=data.pop("end_user_id", None),
            )
            conversation = await get_or_create_owned_conversation(
                db,
                conversation_id=conversation_id,
                user_id=principal["id"],
                external_user_id=conversation_external_user_id(principal),
                workspace=principal.get("workspace", "default"),
            )

        message = data.get("message", "")
        turn_plan = await _plan_conversation_turn(conversation_id, message)
        data["turn_plan"] = turn_plan.model_dump(mode="json")
        data["effective_message"] = turn_plan.effective_message
        data["resumed_from"] = turn_plan.resumed_from
        data["clarified_from"] = turn_plan.clarified_from

        run_state = {
            "run_id": str(uuid.uuid4()),
            "partial_output": "",
            "message": message,
            "effective_message": data.get("effective_message") or message,
            "resumed_from": data.get("resumed_from"),
        }
        async with async_session() as db:
            await save_checkpoint(
                db, run_id=run_state["run_id"], conversation_id=conversation_id,
                owner_id=principal["id"], external_user_id=principal["external_user_id"],
                phase="planned", status="running",
                state={"message": message, "turn_plan": data["turn_plan"]},
            )
        await websocket.send_json({
            "type": "run_started",
            "data": {"run_id": run_state["run_id"], "session_id": conversation_id},
        })

        current_task = asyncio.create_task(_handle_websocket_chat(
            websocket,
            conversation_id,
            data,
            run_state,
            principal,
            conversation.workspace,
        ))
        while not current_task.done():
            receive_task = asyncio.create_task(websocket.receive_json())
            done, pending = await asyncio.wait(
                {current_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if current_task in done:
                receive_task.cancel()
                try:
                    await current_task
                except asyncio.CancelledError:
                    logger.info(f"WebSocket task cancelled: {conversation_id}")
                break

            control_data = receive_task.result()
            if control_data.get("type") == "cancel":
                current_task.cancel()
                partial_output = run_state.get("partial_output", "")
                is_resumable = is_resumable_partial(partial_output)
                if is_resumable:
                    await memory_manager.append(
                        conversation_id,
                        run_state["message"],
                        partial_output,
                        status="interrupted",
                        run_id=run_state["run_id"],
                        metadata={
                            "effective_message": run_state["effective_message"],
                            "resumed_from": run_state.get("resumed_from"),
                            "resumable": True,
                        },
                    )
                async with async_session() as db:
                    await save_checkpoint(
                        db, run_id=run_state["run_id"], conversation_id=conversation_id,
                        owner_id=principal["id"], external_user_id=principal["external_user_id"],
                        phase="stream", status="interrupted" if is_resumable else "cancelled",
                        state={"partial_output": partial_output, "resumable": is_resumable},
                    )
                await websocket.send_json({
                    "type": "interrupted" if is_resumable else "cancelled",
                    "data": {
                        "run_id": run_state["run_id"],
                        "session_id": conversation_id,
                        "partial_output": partial_output,
                        "resumable": is_resumable,
                    },
                })
                try:
                    await current_task
                except asyncio.CancelledError:
                    logger.info(f"WebSocket task cancelled: {conversation_id}")
                break

            await websocket.send_json({
                "type": "error",
                "data": {"code": "BUSY", "message": "当前回复尚未完成，请停止后再发送新问题"},
            })
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {conversation_id}")
        if current_task and not current_task.done():
            current_task.cancel()
    except HTTPException as exc:
        await websocket.send_json({
            "type": "error",
            "data": {"code": "UNAUTHORIZED", "message": exc.detail},
        })
        await websocket.close(code=4401 if exc.status_code == 401 else 4404)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.send_json({
            "type": "error",
            "data": {"code": "INTERNAL", "message": format_llm_error(e)},
        })
