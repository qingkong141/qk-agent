"""Immutable protocol versions, verified activation, HTTP ingress and downloadable workers."""
import hashlib
import io
import json
import uuid
import zipfile
from datetime import timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from app.api.datasets import identity,scope
from app.api.protocol_debug import typed_owned
from app.api.studio import RevisionInput
from app.dependencies import CurrentUser, DbSession
from app.models.protocol_plugin import PluginMessage,PluginVersion,ProtocolPlugin
from app.services import plugin_runtime as runtime

router=APIRouter(prefix='/studio/plugins',tags=['protocol-plugins'])


class CreateInput(BaseModel):
    name: str=Field(min_length=1,max_length=120)
    debug_id: str=Field(min_length=1,max_length=36)


class VersionInput(RevisionInput):
    debug_id: str=Field(min_length=1,max_length=36)


class Activation(RevisionInput):
    version_id: str=Field(min_length=1,max_length=36)


class Packet(BaseModel):
    raw: str=Field(min_length=1,max_length=400000)


async def owned(plugin_id,db,user):
    item=await db.scalar(select(ProtocolPlugin).where(ProtocolPlugin.id==plugin_id,*scope(ProtocolPlugin,user)))
    if not item:raise HTTPException(404,'协议插件不存在')
    return item


async def version_owned(plugin_id,version_id,db,user):
    await owned(plugin_id,db,user)
    version=await db.scalar(select(PluginVersion).where(PluginVersion.id==version_id,PluginVersion.plugin_id==plugin_id))
    if not version:raise HTTPException(404,'插件版本不存在')
    return version


def public(item):
    return {'id':item.id,'name':item.name,'revision':item.revision,'active_version_id':item.active_version_id,'runtime':runtime.status(item.id)}


async def verified_settings(debug_id,db,user):
    item=await typed_owned(debug_id,'protocol_debug',db,user)
    # Server reruns both scripts; a browser "passed" report is not a deployment attestation.
    worker=await runtime.start('validation',item.config)
    await worker.close()
    return item.config


def make_version(plugin_id,number,settings):
    digest=hashlib.sha256(json.dumps(settings,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return PluginVersion(id=str(uuid.uuid4()),plugin_id=plugin_id,number=number,settings=settings,digest=digest)


@router.get('')
async def listing(db: DbSession,user: CurrentUser):
    return {'items':[public(v) for v in await db.scalars(select(ProtocolPlugin).where(*scope(ProtocolPlugin,user)).order_by(ProtocolPlugin.created_at.desc()))],'core':runtime.CORE}


@router.post('',status_code=201)
async def create(data: CreateInput,db: DbSession,user: CurrentUser):
    scope(ProtocolPlugin,user)
    if not data.name.strip():raise HTTPException(400,'请填写插件名称')
    settings=await verified_settings(data.debug_id,db,user)
    item=ProtocolPlugin(id=str(uuid.uuid4()),name=data.name.strip(),revision=1,**identity(user))
    db.add(item);await db.flush();db.add(make_version(item.id,1,settings));await db.commit();await db.refresh(item)
    return public(item)


@router.get('/{plugin_id}')
async def read(plugin_id: str,db: DbSession,user: CurrentUser):
    item=await owned(plugin_id,db,user)
    versions=await db.scalars(select(PluginVersion).where(PluginVersion.plugin_id==plugin_id).order_by(PluginVersion.number.desc()))
    return {**public(item),'core':runtime.CORE,'versions':[{'id':v.id,'number':v.number,'digest':v.digest,'created_at':v.created_at,
        'syntax_name':v.settings['syntax']['name'],'semantic_name':v.settings['semantic']['name'],
        'product_key':v.settings['productKey'],'input_format':v.settings['inputFormat'],'raw':v.settings['raw'],
        'fields':v.settings['semantic']['targetModel']['fields']} for v in versions]}


@router.post('/{plugin_id}/versions',status_code=201)
async def new_version(plugin_id: str,data: VersionInput,db: DbSession,user: CurrentUser):
    async with runtime.lock(plugin_id):
        await owned(plugin_id,db,user);settings=await verified_settings(data.debug_id,db,user)
        changed=await db.execute(update(ProtocolPlugin).where(ProtocolPlugin.id==plugin_id,*scope(ProtocolPlugin,user),ProtocolPlugin.revision==data.expected_revision).values(revision=ProtocolPlugin.revision+1))
        if changed.rowcount!=1:raise HTTPException(409,'插件已修改，请刷新后重试')
        number=(await db.scalar(select(func.max(PluginVersion.number)).where(PluginVersion.plugin_id==plugin_id)) or 0)+1
        version=make_version(plugin_id,number,settings);db.add(version);await db.commit()
    return {'id':version.id,'number':number}


@router.post('/{plugin_id}/start')
async def activate(plugin_id: str,data: Activation,db: DbSession,user: CurrentUser):
    async with runtime.lock(plugin_id):
        version=await version_owned(plugin_id,data.version_id,db,user)
        if len(runtime.registry)>=20 and plugin_id not in runtime.registry:raise HTTPException(409,'本机最多同时运行20个协议插件，请先停止不用的插件')
        worker=await runtime.start(version.id,version.settings)
        try:
            changed=await db.execute(update(ProtocolPlugin).where(ProtocolPlugin.id==plugin_id,*scope(ProtocolPlugin,user),ProtocolPlugin.revision==data.expected_revision).values(active_version_id=version.id,revision=ProtocolPlugin.revision+1))
            if changed.rowcount!=1:raise HTTPException(409,'插件已修改，请刷新后重试')
            await db.commit()
        except BaseException:await worker.close();raise
        await runtime.install(plugin_id,worker)
    return {'runtime':runtime.status(plugin_id),'core':runtime.CORE}


@router.post('/{plugin_id}/stop')
async def stop(plugin_id: str,data: RevisionInput,db: DbSession,user: CurrentUser):
    async with runtime.lock(plugin_id):
        await owned(plugin_id,db,user)
        changed=await db.execute(update(ProtocolPlugin).where(ProtocolPlugin.id==plugin_id,*scope(ProtocolPlugin,user),ProtocolPlugin.revision==data.expected_revision).values(active_version_id=None,revision=ProtocolPlugin.revision+1))
        if changed.rowcount!=1:raise HTTPException(409,'插件已修改，请刷新后重试')
        await db.commit();await runtime.stop(plugin_id)
    return {'runtime':runtime.status(plugin_id),'core':runtime.CORE}


@router.post('/{plugin_id}/ingest')
async def ingest(plugin_id: str,data: Packet,db: DbSession,user: CurrentUser):
    item=await owned(plugin_id,db,user);worker=runtime.registry.get(plugin_id)
    if not item.active_version_id or not worker or worker.version_id!=item.active_version_id:raise HTTPException(409,'插件尚未上线或正在切换，请启动版本后重试')
    failure=None
    try:result={'ok':True,**await worker.request({'raw':data.raw})}
    except HTTPException as error:result={'ok':False,'error':error.detail};failure=error
    entry=PluginMessage(id=str(uuid.uuid4()),plugin_id=plugin_id,version_id=worker.version_id,result=result)
    db.add(entry);await db.commit()
    if failure:raise failure
    return {**result,'id':entry.id,'version_id':worker.version_id,'core':runtime.CORE}


@router.get('/{plugin_id}/messages')
async def messages(plugin_id: str,db: DbSession,user: CurrentUser):
    await owned(plugin_id,db,user)
    items=await db.scalars(select(PluginMessage).where(PluginMessage.plugin_id==plugin_id).order_by(PluginMessage.received_at.desc(),PluginMessage.id.desc()).limit(100))
    return [{'id':i.id,'version_id':i.version_id,'received_at':i.received_at.replace(tzinfo=timezone.utc) if i.received_at.tzinfo is None else i.received_at,**i.result} for i in items]


@router.get('/{plugin_id}/versions/{version_id}/download')
async def download(plugin_id: str,version_id: str,db: DbSession,user: CurrentUser):
    version=await version_owned(plugin_id,version_id,db,user)
    manifest={'plugin_id':plugin_id,'version_id':version.id,'number':version.number,'config_sha256':version.digest,'settings':version.settings}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        for file in ['runtime.cjs','package.json','package-lock.json','node_modules/jsonata/jsonata.js','node_modules/jsonata/package.json','node_modules/jsonata/LICENSE']:
            archive.write(runtime.DIRECTORY/file,file)
        archive.writestr('run.cjs',"const {spawn}=require('node:child_process');const settings=require('./manifest.json').settings;const p=spawn(process.execPath,['--max-old-space-size=128',require('node:path').join(__dirname,'runtime.cjs')],{stdio:['pipe','inherit','inherit']});p.stdin.write(JSON.stringify({command:'initialize',settings})+'\\n');process.stdin.pipe(p.stdin);p.on('exit',code=>process.exit(code||0));")
        archive.writestr('README.md','# 协议转换插件\n\n需要Node.js 22或以上。运行 `node run.cjs`，初始化时验证manifest中的报文并输出ready。随后每行输入一个JSON对象 `{ "raw": "设备报文JSON字符串" }`，逐行输出标准字段。已包含JSONata 2.2.2及许可证，无需安装网络依赖。\n\n工作台中选择版本并启动时，后台管理进程会启动该Worker，收到ready后自动注册并切换在线版本。下载包为独立校验运行包；HTTP收报文、账号验证和持久化记录由工作台后台提供，不包含硬件TCP/MQTT驱动。\n\n配置SHA256在manifest中，不含登录令牌。')
    return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="protocol-{plugin_id}-v{version.number}.zip"'})


@router.delete('/{plugin_id}')
async def remove(plugin_id: str,data: RevisionInput,db: DbSession,user: CurrentUser):
    async with runtime.lock(plugin_id):
        item=await owned(plugin_id,db,user)
        if item.active_version_id:raise HTTPException(409,'请先停止插件再删除')
        changed=await db.execute(update(ProtocolPlugin).where(ProtocolPlugin.id==plugin_id,*scope(ProtocolPlugin,user),ProtocolPlugin.revision==data.expected_revision).values(revision=ProtocolPlugin.revision+1))
        if changed.rowcount!=1:raise HTTPException(409,'插件已修改，请刷新后重试')
        await db.execute(delete(PluginMessage).where(PluginMessage.plugin_id==plugin_id));await db.execute(delete(PluginVersion).where(PluginVersion.plugin_id==plugin_id));await db.execute(delete(ProtocolPlugin).where(ProtocolPlugin.id==plugin_id));await db.commit()
    return {'id':plugin_id}
