"""Single-process platform readers. Session credentials stay in memory only."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.db.session import async_session
from app.models.realtime import RealtimeTask
from app.services.realtime_auth import PlatformSession

tasks = {}
ZONE = timezone(timedelta(hours=8))


def validate_source(item, token):
    if item.config['source']['kind'] != 'platform': return
    if not settings.PLATFORM_DEVICE_BASE_URL: raise HTTPException(503, '后台未配置设备读取服务地址')
    if not token: raise HTTPException(400, '平台读取任务需要使用平台账号登录启动')
    if len(tasks) >= 20: raise HTTPException(429, '后台最多同时运行20个读取任务')


def metric_value(report, metric):
    from json import loads
    payload = loads(report) if isinstance(report, str) else report
    if not isinstance(payload, dict) or not isinstance(payload.get('app'), list): raise ValueError('设备value缺少app指标数组')
    found = []
    def segment(value): return quote(str(value), safe='').replace('.', '%2E')
    for app in payload['app']:
        for group in app.get('data', []):
            for point in group.get('values', []):
                key = 'metric.' + '.'.join(segment(value) for value in (app.get('identifier', ''), group.get('type', ''), point.get('key', '')))
                if key == metric: found.append(point.get('value'))
    if len(found) > 1: raise ValueError('同一设备报文中指标重复')
    value = found[0] if found else None
    if isinstance(value, str): value = float(value) if value.strip() else None
    return value


async def fetch(source, token):
    from app.api.realtime import Batch
    from app.services.realtime_engine import timestamp, iso
    now = datetime.now(ZONE)
    device_id = source['device_id']
    # Platform MAC identifiers are upper-case in DM_Device and lower-case in InfluxDB.
    # Ordinary business identifiers retain their case-sensitive identity.
    hardware_id = bool(re.fullmatch(r'[0-9a-fA-F]{12}', device_id))
    variants = set((device_id, device_id.lower(), device_id.upper())) if hardware_id else {device_id}
    predicate = ' OR '.join(f"deviceId='{value}'" for value in sorted(variants)) if device_id else ''
    params = {'tableName': source['table'], 'startTime': (now-timedelta(minutes=source['lookback_minutes'])).strftime('%Y-%m-%d %H:%M:%S'),
              'endTime': now.strftime('%Y-%m-%d %H:%M:%S'), 'pageIndex': 1, 'pageSize': 50,
              'searchScript': '('+predicate+')' if len(variants)>1 else predicate}
    collected = []
    session = token if isinstance(token, PlatformSession) else None
    access_token = await session.access_token() if session else token
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        while True:
            response = await client.get(settings.PLATFORM_DEVICE_BASE_URL.rstrip('/')+'/InfluxDb/GetDatas', params=params,
                headers={'Authorization': 'Bearer '+access_token.removeprefix('Bearer ').strip(), 'UISystemCode': settings.PLATFORM_SYSTEM_CODE})
            if response.status_code == 401 and session:
                access_token = await session.access_token(force=True)
                response = await client.get(settings.PLATFORM_DEVICE_BASE_URL.rstrip('/')+'/InfluxDb/GetDatas', params=params,
                    headers={'Authorization': 'Bearer '+access_token, 'UISystemCode': settings.PLATFORM_SYSTEM_CODE})
            if response.status_code == 401: raise ValueError('平台登录凭据已失效，请重新登录后启动任务')
            if response.status_code == 403: raise ValueError('当前平台账号没有设备数据读取权限，请联系管理员')
            response.raise_for_status()
            body = response.json()
            if body.get('Status') not in (1, True): raise ValueError('平台设备查询未成功，请检查账号权限和设备服务')
            content = body.get('Content')
            if not isinstance(content, dict): raise ValueError('平台设备返回格式无效')
            total = content.get('count'); rows = content.get('list')
            if total == 0 and rows is None: rows = []
            if not isinstance(rows, list) or not isinstance(total, int) or total < len(rows): raise ValueError('平台设备分页返回格式无效')
            if total > 1000: raise ValueError('本次设备数据超过1,000条，请缩小回看时间或指定设备')
            collected.extend(rows)
            if len(collected) >= total: break
            if not rows or params['pageIndex'] >= 20: raise ValueError('平台分页数据不完整，本轮未计算')
            params['pageIndex'] += 1
    points = [{'deviceId': str(row['deviceId']), 'time': row['time'], 'value': metric_value(row['value'], source['metric'])}
              for row in collected if not device_id or str(row.get('deviceId')) in variants]
    validated = [row.model_dump() for row in Batch(rows=points).rows] if points else []
    # The platform returns newest first, sometimes with MM/DD/YYYY timestamps.
    # Applications use the last point as "latest" and draw trends in input order.
    for row in validated:
        row['time'] = iso(timestamp(row['time']))
    return sorted(validated, key=lambda row: (row['time'], row['deviceId']))


async def loop(item_id, config, token, user):
    from app.api.realtime import owned, process
    try:
        while True:
            rows = await fetch(config['source'], token)
            async with async_session() as db:
                item = await owned(item_id, db, user, True)
                if item.status != 'running': return
                item.runtime = {**item.runtime, 'checked_at': datetime.now(timezone.utc).isoformat()}
                if rows: await process(item, rows, db)
                else:
                    item.runtime = {**item.runtime, 'source_message': '当前读取范围没有新数据'}
                    await db.commit()
            await asyncio.sleep(config['source']['poll_seconds'])
    except asyncio.CancelledError:
        raise
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else '设备读取或计算失败，请检查平台连接后重新启动'
        async with async_session() as db:
            item = await db.get(RealtimeTask, item_id)
            if item and item.status == 'running':
                item.status = 'error'; item.runtime = {**item.runtime, 'error': message[:500], 'checked_at': datetime.now(timezone.utc).isoformat()}
                await db.commit()
    finally:
        if tasks.get(item_id) is asyncio.current_task(): tasks.pop(item_id, None)


def launch(item, token, user, refresh_token=None):
    if item.config['source']['kind'] == 'platform':
        session = PlatformSession(token, refresh_token, user['id']) if refresh_token and user.get('auth_type') == 'platform' else token
        tasks[item.id] = asyncio.create_task(loop(item.id, item.config, session, user))


def cancel(item_id):
    task = tasks.pop(item_id, None)
    if task: task.cancel()


async def startup():
    # Platform credentials are deliberately not stored. Push tasks need no resident loop.
    async with async_session() as db:
        for item in await db.scalars(select(RealtimeTask).where(RealtimeTask.status == 'running')):
            if item.config['source']['kind'] == 'platform':
                item.status = 'stopped'; item.runtime = {**item.runtime, 'error': '服务已重启，请重新启动平台读取任务'}
        await db.commit()


async def shutdown():
    running = list(tasks.values())
    for task in running: task.cancel()
    await asyncio.gather(*running, return_exceptions=True)
    tasks.clear()
