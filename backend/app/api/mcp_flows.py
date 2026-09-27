"""Versioned MCP orchestration, explicit debugging and authenticated published APIs."""
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update

from app.api.datasets import NameInput, identity, scope
from app.api.studio import RevisionInput
from app.dependencies import CurrentUser, DbSession
from app.models.studio import StudioArtifact
from app.services import mcp_flow as runtime

router=APIRouter(prefix='/studio/mcp-flows',tags=['mcp-flows'])
KIND='mcp_flow'


class Input(NameInput):
    config: runtime.Config
    expected_revision: int | None = None


class Run(BaseModel):
    input: dict = Field(default_factory=dict)


class Debug(Run):
    config: runtime.Config


def info(item):
    return {'id':item.id,'name':item.name,'config':item.config,'revision':item.revision,
            'published_revision':item.published_revision,'updated_at':item.updated_at}


async def owned(item_id,db,user):
    item=await db.scalar(select(StudioArtifact).where(StudioArtifact.id==item_id,StudioArtifact.kind==KIND,*scope(StudioArtifact,user)))
    if not item: raise HTTPException(404,'MCP流程不存在')
    return item


@router.get('')
async def listing(db:DbSession,user:CurrentUser):
    return [info(item) for item in await db.scalars(select(StudioArtifact).where(StudioArtifact.kind==KIND,*scope(StudioArtifact,user)).order_by(StudioArtifact.updated_at.desc()))]


@router.post('',status_code=201)
async def create(data:Input,db:DbSession,user:CurrentUser):
    await runtime.registry.validate_services({n.service_id for n in data.config.nodes},db,user)
    item=StudioArtifact(id=str(uuid.uuid4()),name=data.name,kind=KIND,config=data.config.model_dump(by_alias=True),**identity(user))
    db.add(item);await db.commit();await db.refresh(item);return info(item)


@router.post('/debug')
async def debug(data:Debug,request:Request,db:DbSession,user:CurrentUser):
    return await runtime.execute(data.config,data.input,request.headers,db,user)


@router.get('/{item_id}')
async def detail(item_id:str,db:DbSession,user:CurrentUser): return info(await owned(item_id,db,user))


@router.put('/{item_id}')
async def edit(item_id:str,data:Input,db:DbSession,user:CurrentUser):
    item=await owned(item_id,db,user)
    await runtime.registry.validate_services({n.service_id for n in data.config.nodes},db,user)
    changed=await db.execute(update(StudioArtifact).where(StudioArtifact.id==item.id,StudioArtifact.revision==data.expected_revision,*scope(StudioArtifact,user)).values(name=data.name,config=data.config.model_dump(by_alias=True),revision=StudioArtifact.revision+1))
    if changed.rowcount!=1: raise HTTPException(409,'流程已更新，请重新打开')
    await db.commit();await db.refresh(item);return info(item)


@router.post('/{item_id}/publish')
async def publish(item_id:str,data:RevisionInput,request:Request,db:DbSession,user:CurrentUser):
    item=await owned(item_id,db,user)
    config=runtime.Config.model_validate(item.config)
    await runtime.preflight(config,request.headers,db,user)
    changed=await db.execute(update(StudioArtifact).where(StudioArtifact.id==item.id,StudioArtifact.revision==data.expected_revision,*scope(StudioArtifact,user)).values(published_revision=item.revision,published_config=item.config))
    if changed.rowcount!=1: raise HTTPException(409,'流程已更新，请重新打开')
    await db.commit();await db.refresh(item);return info(item)


@router.post('/{item_id}/stop')
async def stop(item_id:str,data:RevisionInput,db:DbSession,user:CurrentUser):
    item=await owned(item_id,db,user)
    changed=await db.execute(update(StudioArtifact).where(StudioArtifact.id==item.id,StudioArtifact.revision==data.expected_revision,*scope(StudioArtifact,user)).values(published_revision=None,published_config=None))
    if changed.rowcount!=1: raise HTTPException(409,'流程已更新，请重新打开')
    await db.commit();await db.refresh(item);return info(item)


@router.delete('/{item_id}')
async def remove(item_id:str,data:RevisionInput,db:DbSession,user:CurrentUser):
    item=await owned(item_id,db,user)
    if item.published_revision: raise HTTPException(409,'请先停用流程API')
    changed=await db.execute(delete(StudioArtifact).where(StudioArtifact.id==item.id,StudioArtifact.revision==data.expected_revision,StudioArtifact.published_revision.is_(None),*scope(StudioArtifact,user)))
    if changed.rowcount!=1: raise HTTPException(409,'流程已更新，请重新打开')
    await db.commit();return {'id':item.id}


@router.post('/{item_id}/invoke')
async def invoke(item_id:str,data:Run,request:Request,db:DbSession,user:CurrentUser):
    item=await owned(item_id,db,user)
    if not item.published_revision: raise HTTPException(409,'流程API尚未发布或已停用')
    return {'revision':item.published_revision,**await runtime.execute(runtime.Config.model_validate(item.published_config),data.input,request.headers,db,user)}
