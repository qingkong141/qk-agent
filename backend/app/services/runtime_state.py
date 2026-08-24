import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runtime import RunCheckpoint, ToolApproval, UserContext


def arguments_hash(arguments: dict) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def save_checkpoint(
    db: AsyncSession,
    *,
    run_id: str,
    conversation_id: str,
    owner_id: str,
    external_user_id: str,
    phase: str,
    status: str,
    state: dict,
    trace_id: str | None = None,
) -> RunCheckpoint:
    last = await db.scalar(
        select(func.max(RunCheckpoint.sequence)).where(RunCheckpoint.run_id == run_id)
    )
    checkpoint = RunCheckpoint(
        id=str(uuid.uuid4()), run_id=run_id, conversation_id=conversation_id,
        owner_id=owner_id, external_user_id=external_user_id, sequence=(last or 0) + 1,
        phase=phase, status=status, state=state, trace_id=trace_id or run_id,
    )
    db.add(checkpoint)
    await db.commit()
    return checkpoint


async def last_checkpoint(
    db: AsyncSession, run_id: str, owner_id: str, external_user_id: str
) -> RunCheckpoint | None:
    return await db.scalar(
        select(RunCheckpoint)
        .where(
            RunCheckpoint.run_id == run_id,
            RunCheckpoint.owner_id == owner_id,
            RunCheckpoint.external_user_id == external_user_id,
        )
        .order_by(RunCheckpoint.sequence.desc())
        .limit(1)
    )


async def request_tool_approval(
    db: AsyncSession, *, owner_id: str, external_user_id: str, conversation_id: str,
    run_id: str | None, tool_name: str, arguments: dict,
) -> ToolApproval:
    digest = arguments_hash(arguments)
    existing = await db.scalar(select(ToolApproval).where(
        ToolApproval.owner_id == owner_id,
        ToolApproval.external_user_id == external_user_id,
        ToolApproval.conversation_id == conversation_id,
        ToolApproval.tool_name == tool_name,
        ToolApproval.arguments_hash == digest,
    ))
    if existing:
        return existing
    approval = ToolApproval(
        id=str(uuid.uuid4()), owner_id=owner_id, external_user_id=external_user_id,
        conversation_id=conversation_id, run_id=run_id, tool_name=tool_name,
        arguments_hash=digest, requested_arguments=arguments,
    )
    db.add(approval)
    await db.commit()
    return approval


async def approve_tool(db: AsyncSession, approval: ToolApproval, approved_by: str) -> ToolApproval:
    if approval.status == "pending":
        approval.status = "approved"
        approval.approved_by = approved_by
        await db.commit()
    return approval


async def consume_tool_approval(
    db: AsyncSession, *, approval_id: str, owner_id: str, external_user_id: str,
    conversation_id: str, tool_name: str, arguments: dict,
) -> str:
    approval = await db.scalar(select(ToolApproval).where(
        ToolApproval.id == approval_id,
        ToolApproval.owner_id == owner_id,
        ToolApproval.external_user_id == external_user_id,
        ToolApproval.conversation_id == conversation_id,
        ToolApproval.tool_name == tool_name,
        ToolApproval.arguments_hash == arguments_hash(arguments),
    ))
    if not approval:
        return "denied"
    if approval.status == "consumed":
        return "already_consumed"
    if approval.status != "approved":
        return "denied"
    approval.status = "consumed"
    approval.consumed_at = datetime.now(timezone.utc)
    await db.commit()
    return "execute"


async def set_user_context(
    db: AsyncSession, owner_id: str, external_user_id: str, workspace: str, content: dict
) -> UserContext:
    row = await db.scalar(select(UserContext).where(
        UserContext.owner_id == owner_id,
        UserContext.external_user_id == external_user_id,
        UserContext.workspace == workspace,
    ))
    if row:
        row.content = content
    else:
        row = UserContext(id=str(uuid.uuid4()), owner_id=owner_id,
                          external_user_id=external_user_id, workspace=workspace, content=content)
        db.add(row)
    await db.commit()
    return row


async def get_user_context(db: AsyncSession, owner_id: str, external_user_id: str, workspace: str) -> dict:
    row = await db.scalar(select(UserContext).where(
        UserContext.owner_id == owner_id,
        UserContext.external_user_id == external_user_id,
        UserContext.workspace == workspace,
    ))
    return row.content if row else {}


async def clear_user_context(db: AsyncSession, owner_id: str, external_user_id: str, workspace: str) -> None:
    row = await db.scalar(select(UserContext).where(
        UserContext.owner_id == owner_id,
        UserContext.external_user_id == external_user_id,
        UserContext.workspace == workspace,
    ))
    if row:
        await db.delete(row)
        await db.commit()
