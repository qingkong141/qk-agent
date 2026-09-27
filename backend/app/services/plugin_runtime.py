"""One isolated Node worker per active plugin; validate before swapping registry entries."""
import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime,timezone
from pathlib import Path

from fastapi import HTTPException

DIRECTORY=Path(__file__).resolve().parents[2]/'protocol-runtime'
CORE={'instance_id':str(uuid.uuid4()),'started_at':datetime.now(timezone.utc).isoformat(),'pid':os.getpid()}
registry={}
errors={}
locks={}


def lock(plugin_id): return locks.setdefault(plugin_id,asyncio.Lock())


class Worker:
    def __init__(self,process,version_id):
        self.process=process;self.version_id=version_id;self.lock=asyncio.Lock();self.started_at=datetime.now(timezone.utc).isoformat()

    async def request(self,payload):
        async with self.lock:
            if self.process.returncode is not None:raise HTTPException(503,'插件进程已停止，请重新启动版本')
            try:
                self.process.stdin.write((json.dumps(payload,ensure_ascii=False)+'\n').encode())
                await self.process.stdin.drain()
                raw=await asyncio.wait_for(self.process.stdout.readline(),5)
                result=json.loads(raw)
            except asyncio.CancelledError:
                if self.process.returncode is None:self.process.kill()
                await self.process.wait()
                raise
            except (TimeoutError,ValueError,BrokenPipeError,ConnectionError):
                if self.process.returncode is None:self.process.kill()
                await self.process.wait()
                raise HTTPException(422,'插件执行超时或进程异常，已停止该插件，请检查脚本后重新启动')
            if result.get('error'):raise HTTPException(422,result['error'])
            return result

    async def close(self):
        async with self.lock:
            if self.process.returncode is None:self.process.kill()
            await self.process.wait()


async def start(version_id,settings):
    node=shutil.which('node')
    if not node or not (DIRECTORY/'node_modules/jsonata/jsonata.js').is_file():raise HTTPException(503,'插件运行环境缺少Node.js或jsonata，请在backend/protocol-runtime执行npm ci')
    process=await asyncio.create_subprocess_exec(node,'--max-old-space-size=128',str(DIRECTORY/'runtime.cjs'),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=1000000)
    worker=Worker(process,version_id)
    try:await worker.request({'command':'initialize','settings':settings})
    except BaseException:await worker.close();raise
    return worker


async def install(plugin_id,worker):
    old=registry.get(plugin_id);registry[plugin_id]=worker;errors.pop(plugin_id,None)
    if old:await old.close()


async def stop(plugin_id):
    old=registry.pop(plugin_id,None);errors.pop(plugin_id,None)
    if old:await old.close()


def status(plugin_id):
    worker=registry.get(plugin_id)
    return {'online':bool(worker and worker.process.returncode is None),'version_id':worker.version_id if worker else None,
            'started_at':worker.started_at if worker else None,'error':errors.get(plugin_id)}


async def startup():
    from sqlalchemy import select
    from app.db.session import async_session
    from app.models.protocol_plugin import ProtocolPlugin,PluginVersion
    async with async_session() as db:
        entries=list(await db.execute(select(ProtocolPlugin,PluginVersion).join(PluginVersion,ProtocolPlugin.active_version_id==PluginVersion.id)))
    for plugin,version in entries:
        try:await install(plugin.id,await start(version.id,version.settings))
        except HTTPException as error:errors[plugin.id]=error.detail


async def shutdown():
    for plugin_id in list(registry):await stop(plugin_id)
    locks.clear()
