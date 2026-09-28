"""Export only the curated, generated case as API inputs, never a database backup."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    runtime = ROOT / 'data/docker/runtime'
    with sqlite3.connect(f'file:{(runtime / "agent.db").as_posix()}?mode=ro', uri=True) as source:
        db = sqlite3.connect(':memory:')
        source.backup(db)
    db.row_factory = sqlite3.Row

    def rows(table, fields):
        return [{k: json.loads(r[k]) if k in ('config', 'fields', 'model_ids') else r[k]
                 for k in fields.split()} for r in db.execute('SELECT * FROM ' + table)]

    data = {'version': 1, 'case_id': 'hospital-equipment-v1', 'sample_date': '2026-09-26'}
    data['datasets'] = [r for r in rows('datasets', 'id name folder_id description')
                        if r['name'] in ('设备运行全流程数据', '操作规范与现场资料')]
    assert len(data['datasets']) == 2, 'Curated datasets are missing'
    dataset_ids = {r['id'] for r in data['datasets']}
    folder_ids = {r['folder_id'] for r in data['datasets']}
    data['folders'] = [r for r in rows('dataset_folders', 'id name') if r['id'] in folder_ids]
    data['files'] = [r for r in rows('dataset_files', 'id name dataset_id modality mime sha256') if r['dataset_id'] in dataset_ids]
    file_ids = {r['id'] for r in data['files']}
    data['models'] = [r for r in rows('data_models', 'id name description domain_id table_name layer fields source_file_id') if r['source_file_id'] in file_ids]
    assert {r['layer'] for r in data['models']} == {'ODS', 'DWD', 'DIM', 'DWS', 'ADS'}
    domain_ids = {r['domain_id'] for r in data['models']}
    data['domains'] = [r for r in rows('theme_domains', 'id name description') if r['id'] in domain_ids]
    data['queries'] = [r for r in rows('offline_queries', 'id name domain_id model_ids sql row_limit') if r['domain_id'] in domain_ids]
    artifacts = rows('studio_artifacts', 'id name kind config')
    data['pipelines'] = [r for r in artifacts if r['kind'] == 'pipeline' and r['name'] != '在线设备业务档案']
    pipeline_ids = {r['id'] for r in data['pipelines']}
    assert all(r['config']['sourceSettings']['kind'] == 'json' for r in data['pipelines'])
    data['services'] = [r for r in artifacts if r['kind'] == 'data_service' and r['config']['pipeline_id'] in pipeline_ids]
    service_ids = {r['id'] for r in data['services']}
    data['applications'] = [r for r in artifacts if r['kind'] == 'application' and r['config']['source']['kind'] == 'data_service' and r['config']['source']['id'] in service_ids]
    assert len(data['applications']) == 6
    data['explorations'] = [r for r in artifacts if r['kind'] == 'exploration' and r['config']['domain_id'] in domain_ids]
    data['converters'] = [r for kind in ('source_model', 'target_model', 'mapping', 'syntax') for r in artifacts if r['kind'] == kind]
    data['debug'] = [r for r in artifacts if r['kind'] == 'protocol_debug']
    data['cloud_models'] = rows('studio_agent_models', 'id name model')
    # No conversations, credentials, owner IDs, public links or real platform rows.
    output = ROOT / 'data/server-case'
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output / 'hospital-case.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for entry in data['files']:
            content = (runtime / 'datasets' / entry['id']).read_bytes()
            assert hashlib.sha256(content).hexdigest() == entry['sha256']
            archive.writestr('files/' + entry['id'], content)
        archive.writestr('case.json', json.dumps(data, ensure_ascii=False))
    shutil.copyfile(ROOT / 'deploy/server/seed_case.py', output / 'seed_case.py')
    shutil.copyfile(ROOT / 'deploy/server/SEED.md', output / '使用说明.md')
    print('Case package:', output)
    print(json.dumps({k: len(v) for k, v in data.items() if isinstance(v, list)}))


if __name__ == '__main__':
    main()
