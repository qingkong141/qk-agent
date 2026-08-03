from fastapi import APIRouter, HTTPException

from app.core.context import set_request_context
from app.core.prompts import wrap_user_input
from app.dependencies import CurrentUser
from app.graph.workflow import get_workflow
from app.memory.manager import memory_manager
from app.schemas.workflow import WorkflowExecuteRequest, WorkflowExecuteResponse, WorkflowInfo
from app.services.message_sources import extract_sources_from_steps

router = APIRouter(prefix="/workflows", tags=["workflows"])

AVAILABLE_WORKFLOWS = [
    WorkflowInfo(
        id="default",
        name="默认多 Agent 工作流",
        description="Supervisor 路由 → 知识检索 / 计算 / 通用回复",
        nodes=["supervisor", "kb_agent", "calc_agent", "response_agent"],
    ),
]


@router.get("", response_model=list[WorkflowInfo])
async def list_workflows(user: CurrentUser):
    return AVAILABLE_WORKFLOWS


@router.post("/{workflow_id}/execute", response_model=WorkflowExecuteResponse)
async def execute_workflow(workflow_id: str, data: WorkflowExecuteRequest, user: CurrentUser):
    if workflow_id != "default":
        raise HTTPException(status_code=404, detail="工作流不存在")

    user_input = data.input.get("message") or data.input.get("query") or ""
    if not user_input:
        raise HTTPException(status_code=400, detail="input 需包含 message 或 query 字段")

    conversation_id = data.conversation_id or data.input.get("conversation_id")
    workspace = user.get("workspace", "default")
    set_request_context(user["id"], workspace)

    chat_history = []
    if conversation_id:
        chat_history = await memory_manager.get_messages(conversation_id)

    workflow = get_workflow()
    result = await workflow.ainvoke({
        "user_input": wrap_user_input(user_input),
        "chat_history": chat_history,
        "user_id": user["id"],
        "workspace": workspace,
        "intent": "",
        "agent_result": "",
        "kb_hit": False,
        "sources": [],
        "intermediate_steps": [],
        "final_output": "",
    })

    output = result.get("final_output", "")
    if conversation_id:
        await memory_manager.append(
            conversation_id,
            user_input,
            output,
            sources=result.get("sources") or extract_sources_from_steps(result.get("intermediate_steps", [])),
        )

    steps = [
        {"tool": step[0].tool if hasattr(step[0], "tool") else str(step[0]), "result": str(step[1])[:200]}
        for step in result.get("intermediate_steps", [])
    ]
    return WorkflowExecuteResponse(
        output=output,
        intent=result.get("intent", ""),
        intermediate_steps=steps,
    )
