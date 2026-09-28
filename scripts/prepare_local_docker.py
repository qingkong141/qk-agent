"""Snapshot the development data for the first local Docker deployment.

Run with backend/venv/Scripts/python.exe. Never overwrites a deployed database.
Connection secrets are written only to the ignored data/docker directory.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
DEST = ROOT / 'data' / 'docker'
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from app.config import settings


def main():
    runtime = DEST / 'runtime'
    if (runtime / 'agent.db').exists():
        raise SystemExit('Docker database already exists; refusing to overwrite it.')
    if not settings.DATABASE_URL.startswith('sqlite+aiosqlite:///'):
        raise SystemExit('This snapshot helper requires a SQLite source.')
    if settings.MCP_STDIO_CONFIG_FILE:
        raise SystemExit('Configure Linux stdio executable paths before migrating.')
    source = Path(settings.DATABASE_URL.removeprefix('sqlite+aiosqlite:///')).resolve()
    if not source.is_file():
        raise SystemExit('Source database does not exist.')
    runtime.mkdir(parents=True, exist_ok=True)
    for name, directory in [('documents', settings.UPLOAD_DIR), ('datasets', settings.DATASET_DIR), ('chroma', './data/chroma')]:
        if Path(directory).exists():
            shutil.copytree(directory, runtime / name, dirs_exist_ok=True)
    (runtime / 'logs').mkdir(exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(runtime / 'agent.db') as dst:
        src.backup(dst)
        # Uploaded document paths were saved on Windows. Dataset files use IDs.
        for doc_id, old in dst.execute('SELECT id, file_path FROM documents').fetchall():
            name = Path(old.replace('\\', '/')).name
            if not (runtime / 'documents' / name).is_file():
                raise RuntimeError('Document file missing for ' + doc_id)
            dst.execute('UPDATE documents SET file_path=? WHERE id=?', ('data/documents/' + name, doc_id))
        dst.commit()
        counts = {name: dst.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                  for name, in dst.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    values = settings.model_dump()
    values.update(DEBUG=False, LOCAL_STUDIO_NO_LOGIN=False,
                  DATABASE_URL='sqlite+aiosqlite:///./data/agent.db',
                  UPLOAD_DIR='./data/documents', DATASET_DIR='./data/datasets', LOG_DIR='./data/logs')
    values['OLLAMA_BASE_URL'] = values['OLLAMA_BASE_URL'].replace('://localhost:', '://host.docker.internal:').replace('://127.0.0.1:', '://host.docker.internal:')
    lines = []
    for key, value in values.items():
        value = str(value).lower() if isinstance(value, bool) else str(value)
        if '\n' in value or '\r' in value:
            raise RuntimeError('Multiline environment value is unsupported: ' + key)
        lines.append(key + '=' + value)
    (DEST / 'agent.env').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (DEST / 'snapshot-counts.json').write_text(json.dumps(counts, indent=2), encoding='utf-8')
    print('Snapshot prepared:', sum(counts.values()), 'records across', len(counts), 'tables.')
    print('Existing development data unchanged. Docker secrets are excluded from Git and images.')


if __name__ == '__main__':
    main()
