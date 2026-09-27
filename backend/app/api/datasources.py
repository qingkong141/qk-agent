import asyncio
import base64
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update

from app.api.datasets import identity, scope
from app.api.studio import RevisionInput
from app.config import settings
from app.dependencies import CurrentUser, DbSession
from app.models.datasource import DataSource
from app.models.studio import StudioArtifact
from app.services.datasource_schema import Connection, ReadInput, TYPES

router=APIRouter(prefix='/studio/datasources',tags=['datasources'])
slots=asyncio.Semaphore(4)


class Input(BaseModel):
    name: str=Field(min_length=1,max_length=120)
    connection: Connection
    password: str|None=Field(default=None,max_length=1000)
    expected_revision: int|None=Field(default=None,ge=1)


def cipher():
    if settings.SECRET_KEY=='change-me-in-production': raise HTTPException(503,'请先配置后台SECRET_KEY后保存数据源密码')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('datasources:'+settings.SECRET_KEY).encode()).digest()))


def secret(password): return cipher().encrypt(password.encode()).decode() if password else ''
def public(item): return {'id':item.id,'name':item.name,'connection':item.connection,'has_password':bool(item.credential),'revision':item.revision,'last_test':item.last_test}


async def owned(item_id,db,user):
    item=await db.scalar(select(DataSource).where(DataSource.id==item_id,*scope(DataSource,user)))
    if not item:raise HTTPException(404,'数据源不存在')
    return item


async def run(item,operation,read=None):
    try: password=cipher().decrypt(item.credential.encode()).decode() if item.credential else ''
    except InvalidToken: raise HTTPException(409,'数据源密码无法解密，请重新保存密码；后台SECRET_KEY可能已变更')
    payload=json.dumps({'connection':item.connection,'password':password,'operation':operation,'read':read.model_dump() if read else None},ensure_ascii=False).encode()
    async with slots:
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.services.datasource_worker',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            output,_=await asyncio.wait_for(process.communicate(payload),30)
        except (TimeoutError,asyncio.CancelledError):
            if process.returncode is None:process.kill()
            await process.wait()
            if asyncio.current_task().cancelling():raise
            raise HTTPException(504,'读取超过30秒，已终止本次连接，请检查网络或缩小范围')
    try: result=json.loads(output.decode('utf-8'))
    except (ValueError,UnicodeError):raise HTTPException(502,'驱动没有返回有效结果，请检查服务版本与连接配置')
    if process.returncode or result.get('error'):raise HTTPException(502,result.get('error','数据源连接失败'))
    return {**result,'read_at':datetime.now(timezone.utc).isoformat(),'source_name':item.name,'source_revision':item.revision}


@router.get('/types')
async def types(user: CurrentUser):
    scope(DataSource,user)
    return [{'id':key,'name':name,'port':port} for key,name,port in TYPES]


@router.get('')
async def listing(db: DbSession,user: CurrentUser):
    return [public(i) for i in await db.scalars(select(DataSource).where(*scope(DataSource,user)).order_by(DataSource.created_at.desc()))]


@router.post('',status_code=201)
async def create(data: Input,db: DbSession,user: CurrentUser):
    scope(DataSource,user)
    if not data.name.strip():raise HTTPException(400,'请填写数据源名称')
    item=DataSource(id=str(uuid.uuid4()),name=data.name.strip(),connection=data.connection.model_dump(),credential=secret(data.password),revision=1,last_test={},**identity(user))
    db.add(item);await db.commit();await db.refresh(item);return public(item)


@router.get('/{item_id}')
async def read(item_id: str,db: DbSession,user: CurrentUser):return public(await owned(item_id,db,user))


@router.put('/{item_id}')
async def edit(item_id: str,data: Input,db: DbSession,user: CurrentUser):
    item=await owned(item_id,db,user)
    if not data.name.strip():raise HTTPException(400,'请填写数据源名称')
    changed=await db.execute(update(DataSource).where(DataSource.id==item_id,*scope(DataSource,user),DataSource.revision==data.expected_revision).values(name=data.name.strip(),connection=data.connection.model_dump(),credential=item.credential if data.password is None else secret(data.password),last_test={},revision=DataSource.revision+1))
    if changed.rowcount!=1:raise HTTPException(409,'配置已被修改，请刷新后重试')
    await db.commit();await db.refresh(item);return public(item)


@router.post('/{item_id}/test')
async def test(item_id: str,db: DbSession,user: CurrentUser):
    item=await owned(item_id,db,user);revision=item.revision
    try:result=await run(item,'test');status={'ok':True,'at':result['read_at'],'message':'连接成功'}
    except HTTPException as error:status={'ok':False,'at':datetime.now(timezone.utc).isoformat(),'message':error.detail}
    await db.execute(update(DataSource).where(DataSource.id==item.id,*scope(DataSource,user),DataSource.revision==revision).values(last_test=status));await db.commit()
    return status


@router.get('/{item_id}/resources')
async def resources(item_id: str,db: DbSession,user: CurrentUser):return await run(await owned(item_id,db,user),'catalog')


@router.post('/{item_id}/extract')
async def extract(item_id: str,data: ReadInput,db: DbSession,user: CurrentUser):return await run(await owned(item_id,db,user),'read',data)


@router.delete('/{item_id}')
async def remove(item_id: str,data: RevisionInput,db: DbSession,user: CurrentUser):
    await owned(item_id,db,user)
    pipelines=await db.scalars(select(StudioArtifact).where(*scope(StudioArtifact,user),StudioArtifact.kind=='pipeline'))
    if any(i.config.get('sourceSettings',{}).get('datasource_id')==item_id for i in pipelines):raise HTTPException(409,'数据源仍被管道引用，请先修改或删除引用管道')
    changed=await db.execute(delete(DataSource).where(DataSource.id==item_id,*scope(DataSource,user),DataSource.revision==data.expected_revision))
    if changed.rowcount!=1:raise HTTPException(409,'配置已被修改，请刷新后重试')
    await db.commit();return {'id':item_id}
