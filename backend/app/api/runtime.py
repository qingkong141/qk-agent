from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.dependencies import CurrentUser, DbSession
from app.models.runtime import RunCheckpoint, ToolApproval
from app.services.conversation_access import conversation_external_user_id
from app.services.runtime_state import approve_tool, last_checkpoint

router = APIRouter(tags=["runtime"])


@router.get("/runs/{run_id}/checkpoint")
async def get_run_checkpoint(run_id: str, user: CurrentUser, db: DbSession):
    checkpoint = await last_checkpoint(db, run_id, user["id"], conversation_external_user_id(user))
    if not checkpoint:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {
        "run_id": checkpoint.run_id,
        "trace_id": checkpoint.trace_id,
        "sequence": checkpoint.sequence,
        "phase": checkpoint.phase,
        "status": checkpoint.status,
        "state": checkpoint.state,
    }


@router.post("/tool-approvals/{approval_id}/approve")
async def approve_tool_request(approval_id: str, user: CurrentUser, db: DbSession):
    approval = await db.scalar(select(ToolApproval).where(
        ToolApproval.id == approval_id,
        ToolApproval.owner_id == user["id"],
        ToolApproval.external_user_id == conversation_external_user_id(user),
    ))
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    await approve_tool(db, approval, user["id"])
    return {"approval_id": approval.id, "status": approval.status}


@router.post("/tool-approvals/{approval_id}/reject")
async def reject_tool_request(approval_id: str, user: CurrentUser, db: DbSession):
    approval = await db.scalar(select(ToolApproval).where(
        ToolApproval.id == approval_id,
        ToolApproval.owner_id == user["id"],
        ToolApproval.external_user_id == conversation_external_user_id(user),
    ))
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if approval.status == "pending":
        approval.status = "rejected"
        approval.approved_by = user["id"]
        await db.commit()
    return {"approval_id": approval.id, "status": approval.status}
