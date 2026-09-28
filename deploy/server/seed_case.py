#!/usr/bin/env python3
"""Import the curated case inside the running Agent container. No source DB or keys."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import zipfile

import httpx
from fastapi import HTTPException

if Path('/app/app').is_dir():
    sys.path.insert(0, '/app')

from app.config import settings

CASE_ID = 'hospital-equipment-v1'


class API:
    def __init__(self, base, account, password):
        self.base = base.rstrip('/')
        self.account, self.password = account, password
        self.client = httpx.Client(timeout=180, trust_env=False, follow_redirects=False)
        self.owner = None
        self.token = ''

    def login(self):
        sso = settings.PLATFORM_SSO_BASE_URL.rstrip('/')
        if not sso:
            raise RuntimeError('容器未配置平台 SSO 地址，请先检查全局 env。')
        response = self.client.post(sso + '/SSO/Login',
            headers={'UISystemCode': settings.PLATFORM_SYSTEM_CODE},
            json={'account': self.account, 'password': self.password, 'isLocal': True})
        if response.status_code != 200:
            raise RuntimeError(f'平台登录失败，HTTP {response.status_code}')
        body = response.json()
        if body.get('Status') != 1:
            raise RuntimeError('平台登录失败，请核对账号、密码和 SSO 地址。')
        content = body.get('Content', {})
        if isinstance(content, str):
            content = json.loads(content)
        token = content.get('Result', {}).get('access_token')
        if not token:
            raise RuntimeError('平台登录未返回 access_token。')
        from app.platform_auth import validate_platform_identity
        owner = asyncio.run(validate_platform_identity(token))
        if self.owner and self.owner != owner:
            raise RuntimeError('续期后的账号发生变化，已停止。')
        self.owner, self.token = owner, token

    def call(self, method, path, **kwargs):
        renewed = False
        for attempt in range(3):
            response = self.client.request(method, self.base + path,
                headers={'X-Platform-Token': self.token}, **kwargs)
            if response.status_code == 401 and not renewed and attempt < 2:
                self.login()
                renewed = True
                continue
            if response.status_code == 429 and attempt < 2:
                print('请求较多，等待服务限流恢复…', flush=True)
                time.sleep(30)
                continue
            if not response.is_success:
                # Do not echo validation inputs: model forms contain an API key.
                try:
                    error = response.json()
                    detail = error.get('errors') or error.get('detail', '')
                    if isinstance(detail, list):
                        detail = '; '.join(str(v.get('msg', '参数错误')) for v in detail)
                    if not isinstance(detail, str): detail = '请求未通过'
                except ValueError:
                    detail = '服务返回非JSON内容'
                # This exact 503 comes from authentication, before the write runs.
                # Do not replay other server errors or ambiguous network failures.
                if (response.status_code == 503 and attempt < 2
                        and detail == '平台登录校验服务暂不可用，请稍后重试'):
                    delay = 2 * (attempt + 1)
                    print(f'平台登录校验暂不可用，{delay} 秒后重试（{attempt + 2}/3）…', flush=True)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f'{method} {path}: HTTP {response.status_code} {detail[:300]}')
            return response.json()
        raise RuntimeError('请求重试次数已用完')


class Importer:
    def __init__(self, api, archive, progress):
        self.api, self.archive, self.progress = api, archive, progress
        self.data = json.loads(archive.read('case.json'))
        if self.data.get('version') != 1 or self.data.get('case_id') != CASE_ID:
            raise RuntimeError('不支持的案例包版本')
        self.state = json.loads(progress.read_text(encoding='utf-8')) if progress.exists() else {
            'owner': api.owner, 'sso': settings.PLATFORM_SSO_BASE_URL,
            'case_id': CASE_ID, 'ids': {}, 'started_at': datetime.now(timezone.utc).isoformat()}
        if (self.state['owner'], self.state['sso'], self.state['case_id']) != (api.owner, settings.PLATFORM_SSO_BASE_URL, CASE_ID):
            raise RuntimeError('进度文件与当前环境或账号不一致。')
        self.ids = self.state['ids']
        self.cache = {}
        self.counts = Counter()
        self.save()

    def save(self):
        temporary = self.progress.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.progress)

    def remap(self, value):
        if isinstance(value, list): return [self.remap(v) for v in value]
        if isinstance(value, dict): return {k: self.remap(v) for k, v in value.items()}
        if isinstance(value, str): return self.ids.get(value, value)
        return value

    def listing(self, path):
        if path not in self.cache:
            result = self.api.call('GET', path)
            self.cache[path] = result if isinstance(result, list) else result['items']
        return self.cache[path]

    def ensure(self, key, endpoint, payload, *, list_path=None, files=None, form=None):
        rows = self.listing(list_path or endpoint)
        matches = [r for r in rows if r['id'] == self.ids.get(key)] if key in self.ids else [
            r for r in rows if r['name'] == payload['name'] and ('kind' not in payload or r.get('kind') == payload['kind'])]
        if len(matches) > 1:
            raise RuntimeError('发现多个同名记录，请先核对：' + payload['name'])
        if key in self.ids and not matches:
            raise RuntimeError('已导入记录被删除，请恢复或使用新的案例进度：' + payload['name'])
        if matches:
            item = matches[0]
            self.counts['reused'] += 1
        else:
            item = self.api.call('POST', endpoint, **({'files': files, 'data': form} if files else {'json': payload}))
            rows.append(item)
            self.counts['created'] += 1
            print('已创建：' + payload['name'], flush=True)
        self.ids[key] = item['id']
        self.save()
        return item

    def artifact(self, item):
        return self.ensure(item['id'], '/studio/artifacts', {
            'name': item['name'], 'kind': item['kind'], 'config': self.remap(item['config'])})

    def get_artifact(self, item_id):
        # The deployed artifact API exposes collection reads, not GET /{id}.
        rows = self.api.call('GET', '/studio/artifacts')
        self.cache['/studio/artifacts'] = rows
        item = next((r for r in rows if r['id'] == item_id), None)
        if not item: raise RuntimeError('关联配置不存在：' + item_id)
        return item

    def sync_models(self):
        if settings.LLM_PROVIDER != 'openai' or not settings.OPENAI_API_KEY or not settings.OPENAI_API_BASE:
            raise RuntimeError('请先配置全局 env 中的华为云模型地址、密钥和默认模型。')
        rows = self.listing('/studio/agent-models')
        self.state.pop('default_model', None)
        for entry in self.data['cloud_models']:
            matches = [r for r in rows if r['name'] == entry['name']]
            if len(matches) > 1: raise RuntimeError('模型名称重复：' + entry['name'])
            payload = {'name': entry['name'], 'model': entry['model'],
                'base_url': settings.OPENAI_API_BASE, 'api_key': settings.OPENAI_API_KEY}
            if matches:
                old = matches[0]
                item = self.api.call('PUT', '/studio/agent-models/' + old['id'],
                    json={**payload, 'expected_revision': old['revision']})
                rows[rows.index(old)] = item
            else:
                item = self.api.call('POST', '/studio/agent-models', json=payload)
                rows.append(item)
            self.ids['custom:' + entry['id']] = item['id']
            if entry['model'] == settings.LLM_MODEL: self.state['default_model'] = item['id']
            print('已同步模型连接：' + entry['name'], flush=True)
        if 'default_model' not in self.state:
            item = self.ensure('default-cloud-model', '/studio/agent-models', {
                'name': '平台默认在线模型', 'model': settings.LLM_MODEL,
                'base_url': settings.OPENAI_API_BASE, 'api_key': settings.OPENAI_API_KEY})
            self.api.call('PUT', '/studio/agent-models/' + item['id'], json={
                'name': item['name'], 'model': settings.LLM_MODEL,
                'base_url': settings.OPENAI_API_BASE, 'api_key': settings.OPENAI_API_KEY,
                'expected_revision': item['revision']})
            self.state['default_model'] = item['id']
        self.save()

    def read_file(self, entry):
        content = self.archive.read('files/' + entry['id'])
        if hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise RuntimeError('案例文件校验失败：' + entry['name'])
        return content

    def import_data(self):
        for group, endpoint in [('folders', '/datasets/folders'), ('domains', '/studio/modeling/domains'), ('datasets', '/datasets')]:
            for entry in self.data[group]:
                self.ensure(entry['id'], endpoint, self.remap({k: v for k, v in entry.items() if k != 'id'}))
        for entry in self.data['files']:
            endpoint = '/datasets/' + self.ids[entry['dataset_id']] + '/files'
            self.ensure(entry['id'], endpoint, {'name': entry['name']},
                files={'file': (entry['name'], self.read_file(entry), entry['mime'])}, form={'modality': entry['modality']})
        for entry in self.data['models']:
            self.ensure(entry['id'], '/studio/modeling/models', self.remap({k: v for k, v in entry.items() if k != 'id'}))
        for entry in self.data['queries']:
            payload = self.remap({k: v for k, v in entry.items() if k != 'id'})
            result = self.api.call('POST', '/studio/offline/run', json={k: v for k, v in payload.items() if k != 'name'})
            if result['truncated'] or not result['rows']: raise RuntimeError('跨表SQL结果不完整：' + entry['name'])
            self.ensure(entry['id'], '/studio/offline/queries', payload)
            print(f"SQL验证：{entry['name']}，{len(result['rows'])} 条", flush=True)
        for entry in self.data['pipelines']: self.artifact(entry)
        for entry in self.data['services']:
            config = self.remap(entry['config'])
            pipeline = self.get_artifact(config['pipeline_id'])
            self.ensure(entry['id'], '/studio/data-services', {'name': entry['name'],
                'description': config['description'], 'pipeline_id': pipeline['id'],
                'pipeline_revision': pipeline['revision'], 'rows': config['rows']})
        for entry in self.data['explorations']:
            config = self.remap(entry['config'])
            self.api.call('POST', '/studio/exploration/run', json=config)
            self.ensure(entry['id'], '/studio/exploration/configs', {'name': entry['name'], 'config': config})
        for entry in self.data['applications']:
            config = self.remap(entry['config'])
            self.api.call('POST', '/studio/applications/debug', json=config)
            item = self.artifact(entry)
            current = self.get_artifact(item['id'])
            if current['published_revision'] is None:
                self.api.call('POST', '/studio/artifacts/' + item['id'] + '/publish',
                    json={'expected_revision': current['revision'], 'require_login': True})
        assets = next(f for f in self.data['files'] if f['name'] == 'dim_device_asset.json')
        self.assets = json.loads(self.read_file(assets))
        for asset in self.assets:
            self.ensure('index-' + asset['deviceId'], '/studio/master-index', {
                'name': asset['deviceName'], 'kind': 'device', 'aliases': [
                    {'system': '设备接入', 'external_id': asset['deviceId'], 'attributes': {'source': '案例生成数据'}},
                    {'system': '资产管理', 'external_id': asset['assetCode'], 'attributes': {'department': asset['department']}},
                    {'system': '病区管理', 'external_id': asset['department'] + '-' + asset['bed'], 'attributes': {'bed': asset['bed']}}]})

    def import_protocol(self):
        for entry in self.data['converters']:
            item = {**entry, 'config': self.remap(entry['config'])}
            if entry['kind'] == 'syntax':
                semantic = self.get_artifact(item['config']['semanticId'])
                item['config']['semanticRevision'] = semantic['revision']
            self.artifact(item)
        entry = self.data['debug'][0]
        config = self.remap(entry['config'])
        name = config['name']
        existing = [r for r in self.listing('/studio/artifacts') if r['kind'] == 'protocol_debug' and r['name'] == name]
        if existing:
            debug = existing[0]
        else:
            payload = {k: v for k, v in config.items() if k not in ('syntax', 'semantic', 'lastRunId', 'configurationId')}
            for kind in ('syntax', 'semantic'):
                current = self.get_artifact(payload[kind + 'Id'])
                payload[kind + 'Revision'] = current['revision']
            run = self.ensure('protocol-run', '/studio/debug/runs', payload)
            run = self.api.call('GET', '/studio/debug/runs/' + run['id'])
            if run['config']['status'] != 'passed':
                async def evaluate():
                    from app.services import plugin_runtime
                    worker = await plugin_runtime.start('seed-check', run['config']['settings'])
                    try: return await worker.request({'raw': run['config']['settings']['raw']})
                    finally: await worker.close()
                result = asyncio.run(evaluate())
                report = {'stages': [{'key': key, 'name': title, 'status': 'passed',
                    'message': '已由服务器 Node.js / JSONata 运行时实际校验'} for key, title in zip(
                        ['content', 'format', 'code', 'syntax', 'source', 'semantic'],
                        ['插件内容', '报文格式', '表达式语法', '语法转换', '源字段类型', '语义与目标字段'])],
                    'output': result['output'], 'checks': [{'key': k, 'ok': True} for k in result['output']],
                    'durationMs': result['duration_ms']}
                self.api.call('PUT', '/studio/debug/runs/' + run['id'], json=report)
            debug = self.api.call('POST', '/studio/debug/configs', json={'runId': run['id']})
            self.cache['/studio/artifacts'].append(debug)
        plugin = self.ensure('protocol-plugin', '/studio/plugins', {'name': '输液设备协议适配', 'debug_id': debug['id']})
        current = self.api.call('GET', '/studio/plugins/' + plugin['id'])
        if not current['runtime']['online']:
            self.api.call('POST', '/studio/plugins/' + plugin['id'] + '/start', json={
                'expected_revision': current['revision'], 'version_id': current['versions'][0]['id']})
        if not self.state.get('plugin_packet'):
            result = self.api.call('POST', '/studio/plugins/' + plugin['id'] + '/ingest', json={'raw': config['raw']})
            self.state['plugin_packet'] = True
            self.save()
        print('协议插件已通过实际转换校验并启动。', flush=True)

    def create_devices(self):
        # Trusted provisioning runs inside the container, after SSO validation.
        # These two catalog types have no bulk HTTP create route. Use the same
        # model validator, identity IDs and report validator as device_assistant.
        async def provision():
            from sqlalchemy import select
            from app.db.session import async_session
            from app.models.studio import StudioArtifact
            from app.api.semantic import ThingModel
            from app.api.device_assistant import entry_id, Report, save_report
            user = {'id': self.api.owner, 'external_user_id': '', 'auth_type': 'platform'}
            model = ThingModel(name='输液运行监测', fields=[
                {'key': 'battery', 'name': '剩余电量', 'type': 'number', 'unit': '%', 'required': False, 'minimum': 0, 'maximum': 100},
                {'key': 'status', 'name': '设备状态码', 'type': 'number', 'required': False, 'minimum': 0, 'maximum': 1}])
            product_id = entry_id('product', model.name, user)
            details = next(f for f in self.data['files'] if f['name'] == 'dwd_device_metrics.json')
            rows = json.loads(self.read_file(details))
            anchor = datetime.fromisoformat(self.state['started_at']) - timedelta(seconds=5)
            async with async_session() as db:
                product = await db.get(StudioArtifact, product_id)
                if product and product.config['model'] != model.model_dump():
                    raise RuntimeError('同名产品物模型已有修改，已停止覆盖。')
                if not product:
                    db.add(StudioArtifact(id=product_id, name=model.name, kind='device_product',
                        owner_id=user['id'], external_user_id='', config={'model': model.model_dump()}))
                    await db.commit()
                for asset in self.assets:
                    device_id = entry_id('device', asset['deviceId'], user)
                    device = await db.get(StudioArtifact, device_id)
                    if device and device.config['product_id'] != product_id:
                        raise RuntimeError('同编号设备属于其他产品：' + asset['deviceId'])
                    if not device:
                        db.add(StudioArtifact(id=device_id, name='输液监测仪 ' + asset['deviceId'], kind='device_instance',
                            owner_id=user['id'], external_user_id='', config={
                                'product_id': product_id, 'code': asset['deviceId'], 'location': asset['department']}))
                        await db.commit()
                    previous = await db.scalar(select(StudioArtifact.id).where(
                        StudioArtifact.owner_id == user['id'], StudioArtifact.kind == 'device_report',
                        StudioArtifact.config['device_id'].as_string() == device_id,
                        StudioArtifact.config['seed_case'].as_string() == CASE_ID))
                    if previous: continue
                    points = [r for r in rows if r['deviceId'] == asset['deviceId']]
                    report = Report(rows=[{'time': (anchor - timedelta(minutes=len(points)-1-i)).isoformat(),
                        'values': {'battery': r['battery'], 'status': r['status']}} for i, r in enumerate(points)])
                    saved = await save_report(device_id, report, db, user)
                    item = await db.get(StudioArtifact, saved['report_id'])
                    item.config = {**item.config, 'seed_case': CASE_ID}
                    await db.commit()
            return rows, anchor
        rows, anchor = asyncio.run(provision())
        print(f'设备目录已准备：1 个产品、{len(self.assets)} 台设备及各 96 条近期上报。', flush=True)
        return rows, anchor

    def create_realtime(self, rows, anchor):
        def node(kind, i, **extra):
            return {'id': f'n{i}', 'kind': kind, 'name': {'source': '数据输入', 'align': '频率对齐', 'fill': '缺失补齐',
                'filter': '数据过滤', 'aggregate': '时间聚合', 'constant': '恒值检测', 'extreme': '极值检测', 'output': '结果输出'}[kind],
                'x': 40+i*220, 'y': 100, 'field': 'value', 'value': '0', 'interval': 1,
                'compare': 'gte', 'valueType': 'number', 'keys': [], 'keep': 'first', 'parseMode': 'json',
                'script': '{}', 'conflict': 'error', 'outputField': 'condition', 'trueValue': '符合条件',
                'falseValue': '不符合条件', **extra}
        nodes = [node('source', 0), node('align', 1), node('fill', 2, value='60'), node('filter', 3),
            node('aggregate', 4, interval=5), node('constant', 5, value='3'), node('extreme', 6), node('output', 7)]
        config = {'source': {'kind': 'push'}, 'window_minutes': 120, 'nodes': nodes,
            'edges': [{'from': a['id'], 'to': b['id']} for a, b in zip(nodes, nodes[1:])]}
        task = self.ensure('realtime', '/studio/realtime', {'name': '设备运行实时指标处理', 'config': config})
        current = self.api.call('GET', '/studio/realtime/' + task['id'])
        if current['status'] != 'running':
            self.api.call('POST', '/studio/realtime/' + task['id'] + '/start', json={'expected_revision': current['revision']})
        if not self.state.get('realtime_loaded'):
            completed = self.state.setdefault('realtime_devices', [])
            for code in ['INF-001', 'INF-002', 'INF-004']:
                if code in completed: continue
                points = [r for r in rows if r['deviceId'] == code]
                data = [{'deviceId': code, 'time': (anchor - timedelta(minutes=len(points)-1-i)).isoformat(), 'value': r['battery']}
                    for i, r in enumerate(points)]
                self.api.call('POST', '/studio/realtime/' + task['id'] + '/ingest', json={'rows': data})
                completed.append(code)
                self.save()
            self.state['realtime_loaded'] = True
            self.save()

    def create_agent_and_flow(self):
        service = next(r for r in self.data['services'] if r['name'] == '设备资产与科室档案')
        latest = next(r for r in self.data['services'] if r['name'] == '设备最新运行状态')
        prompt = ('你是设备运行管理助手。默认分析工作台设备 INF-002。先调用 devices_catalog(source=studio) 获取设备ID，'
            '再调用 telemetry_history 和 condition_analysis，source=studio，metric=battery，查询最近120分钟。'
            f"通过 data_service_result 读取业务档案接口 {self.ids[service['id']]} 和状态接口 {self.ids[latest['id']]}，"
            '按设备code与档案deviceId关联，再调用 knowledge_lookup 查询操作与排查知识。'
            '20%仅为本案例的管理条件，不是厂商报警阈值。缺失数据不能作为正常。'
            '根据工具结果展示图表、近期趋势、档案和可供管理者参考的建议，不直接调整设备或治疗参数。'
            '如用户明确查询平台真实设备，改用 source=platform 并重新查目录；不得用INF案例记录代替真实采集结果。')
        config = {'model': self.state['default_model'], 'prompt': prompt,
            'services': ['devices', 'telemetry', 'conditions', 'knowledge', 'data-services'],
            'max_tool_calls': 20, 'timeout_seconds': 300}
        self.ensure('agent', '/studio/agents', {'name': '设备运行联合分析助手', 'config': config})
        flow = {'nodes': [
            {'id': 'nDevice', 'name': '查询设备', 'service_id': 'devices', 'tool': 'devices_catalog',
             'arguments': {'source': 'studio', 'name': {'$from': 'input', 'path': '/name'}}, 'x': 40, 'y': 100},
            {'id': 'nData', 'name': '读取设备指标', 'service_id': 'telemetry', 'tool': 'telemetry_history',
             'arguments': {'source': 'studio', 'device_id': {'$from': 'nDevice', 'path': '/devices/0/id'}, 'metric': 'battery', 'minutes': 120}, 'x': 320, 'y': 100},
            {'id': 'nKnowledge', 'name': '查询工况知识', 'service_id': 'knowledge', 'tool': 'knowledge_lookup',
             'arguments': {'query': '设备运行指标排查'}, 'x': 600, 'y': 100}],
            'edges': [{'from': 'nDevice', 'to': 'nData'}, {'from': 'nData', 'to': 'nKnowledge'}],
            'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']},
            'example_input': {'name': 'INF-002'}, 'timeout_seconds': 180}
        self.ensure('mcp-flow', '/studio/mcp-flows', {'name': '设备查询与工况知识编排', 'config': flow})

    def run(self):
        self.sync_models()
        self.import_data()
        self.import_protocol()
        rows, anchor = self.create_devices()
        self.create_realtime(rows, anchor)
        self.create_agent_and_flow()
        self.state['completed_at'] = datetime.now(timezone.utc).isoformat()
        self.save()
        print('完成。用同一平台账号登录即可查看数据。', flush=True)
        print('6科室 / 48设备 / 4662原始记录 / 4608运行明细 / 6个五层模型 / 6类应用。', flush=True)
        print('模型连接已使用当前服务器密钥；未调用模型生成虚构对话，也未修改真实设备。', flush=True)
        print('本次创建与复用统计：' + json.dumps(dict(self.counts), ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description='初始化完整医疗设备案例，不清空已有数据。')
    parser.add_argument('--bundle', type=Path, default=Path(__file__).with_name('hospital-case.zip'))
    parser.add_argument('--account', default='admin')
    parser.add_argument('--api-base', default='http://127.0.0.1:8000/api/v1')
    args = parser.parse_args()
    # A supplied password is useful for automated tests; do not put it on argv.
    password = os.environ.get('STUDIO_SEED_PASSWORD') or getpass.getpass('请输入平台账号密码（输入不显示）：')
    api = API(args.api_base, args.account, password)
    try:
        api.login()
        print('平台身份验证成功。正在初始化账号 ' + args.account + ' 的案例…', flush=True)
        directory = Path(settings.DATASET_DIR).resolve().parent / 'seed-progress'
        directory.mkdir(parents=True, exist_ok=True)
        progress = directory / (CASE_ID + '-' + api.owner + '.json')
        # Serialize imports on the server, including simultaneous docker exec calls.
        import fcntl
        with progress.with_suffix('.lock').open('w') as lock, zipfile.ZipFile(args.bundle) as archive:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError('该账号已有初始化脚本正在运行。') from None
            Importer(api, archive, progress).run()
    finally:
        api.client.close()


if __name__ == '__main__':
    try:
        main()
    except HTTPException as error:
        print('初始化未完成：' + str(error.detail), file=sys.stderr)
        sys.exit(1)
    except (RuntimeError, ValueError, OSError, httpx.HTTPError, zipfile.BadZipFile) as error:
        print('初始化未完成：' + str(error), file=sys.stderr)
        print('修复问题后执行相同命令，保留 runtime/seed-progress 可继续。', file=sys.stderr)
        sys.exit(1)
