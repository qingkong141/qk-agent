import json
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from app.dependencies import CurrentUser, DbSession
from app.models.agent_config import AgentConfig
from app.schemas.agent import AgentConfigCreate, AgentConfigResponse, AgentConfigUpdate

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentConfigResponse])
async def list_agents(db: DbSession, user: CurrentUser):
    result = await db.execute(select(AgentConfig).order_by(AgentConfig.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=AgentConfigResponse)
async def create_agent(data: AgentConfigCreate, db: DbSession, user: CurrentUser):
    agent = AgentConfig(
        id=str(uuid.uuid4()),
        name=data.name,
        role=data.role,
        capabilities=data.capabilities,
        system_prompt=data.system_prompt,
        tools=json.dumps(data.tools),
        is_default=data.is_default,
    )
    if data.is_default:
        await db.execute(update(AgentConfig).values(is_default=False))
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=AgentConfigResponse)
async def get_agent(agent_id: str, db: DbSession, user: CurrentUser):
    result = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agent


@router.put("/{agent_id}", response_model=AgentConfigResponse)
async def update_agent(agent_id: str, data: AgentConfigUpdate, db: DbSession, user: CurrentUser):
    result = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    if data.is_default:
        await db.execute(update(AgentConfig).values(is_default=False))

    for field in ("name", "role", "capabilities", "system_prompt", "is_default"):
        value = getattr(data, field)
        if value is not None:
            setattr(agent, field, value)
    if data.tools is not None:
        agent.tools = json.dumps(data.tools)

    await db.commit()
    await db.refresh(agent)
    return agent


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, db: DbSession, user: CurrentUser):
    result = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if agent.is_default:
        raise HTTPException(status_code=400, detail="不能删除默认 Agent")
    await db.delete(agent)
    await db.commit()
    return {"ok": True}
