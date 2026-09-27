"""Explicit local workbench reset; connection tables and upstream databases are retained.

Run from backend with the server stopped. Defaults to a read-only inventory.
"""
import argparse
import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TABLES = (
    'studio_plugin_messages', 'studio_plugin_versions', 'studio_protocol_plugins',
    'studio_master_aliases', 'studio_master_indices', 'realtime_tasks',
    'offline_queries', 'data_models', 'theme_domains', 'dataset_files',
    'datasets', 'dataset_folders', 'studio_artifacts',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--huawei-model-id')
    args = parser.parse_args()
    db_path = Path('data/agent.db').resolve()
    db = sqlite3.connect(f'file:{db_path.as_posix()}?mode=rw', uri=True)
    db.row_factory = sqlite3.Row
    snapshot = {table: [dict(row) for row in db.execute(f'SELECT * FROM {table}')] for table in TABLES}
    print(json.dumps({table: len(rows) for table, rows in snapshot.items()}))
    if not args.apply:
        return
    if not args.backup_dir or not args.huawei_model_id:
        parser.error('--apply requires --backup-dir and --huawei-model-id')
    from app.api.agent_models import cipher
    from app.config import settings
    model = db.execute('SELECT * FROM studio_agent_models WHERE id=?', (args.huawei_model_id,)).fetchone()
    if not model or model['base_url'] != 'https://api.modelarts-maas.com/openai/v1':
        raise ValueError('Choose a configured Huawei MaaS model')
    key = cipher().decrypt(model['credential'].encode()).decode()
    backup = args.backup_dir.resolve()
    backup.mkdir(parents=True, exist_ok=False)
    encoded = json.dumps(snapshot, ensure_ascii=False, indent=2)
    (backup / 'business-records.json').write_text(encoded, encoding='utf-8')
    assert json.loads((backup / 'business-records.json').read_text(encoding='utf-8')) == snapshot
    files_root = Path(settings.DATASET_DIR).resolve()
    (backup / 'files').mkdir()
    paths = []
    for row in snapshot['dataset_files']:
        path = files_root / str(uuid.UUID(row['id']))
        assert path.parent == files_root
        if path.is_file():
            target = backup / 'files' / path.name
            shutil.copy2(path, target)
            assert target.read_bytes() == path.read_bytes()
            paths.append(path)
    # Connection credentials are deliberately excluded from the backup.
    from dotenv import set_key
    for env in (Path('.env'), Path('../.env')):
        if env.exists():
            for name, value in {'LLM_PROVIDER': 'openai', 'OPENAI_API_BASE': model['base_url'],
                                'OPENAI_API_KEY': key, 'LLM_MODEL': model['model']}.items():
                set_key(str(env), name, value)
    with db:
        for table in TABLES:
            db.execute(f'DELETE FROM {table}')
        removed = db.execute("DELETE FROM studio_agent_models WHERE base_url LIKE 'https://api.deepseek.com%'").rowcount
    for path in paths:
        path.unlink()
    print(json.dumps({'backup': str(backup), 'removed_personal_models': removed,
                      'default_model': model['model'], 'remaining_business_records':
                      sum(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES)}))


if __name__ == '__main__':
    main()
