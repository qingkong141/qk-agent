"""Build an Agent-only server package; secrets remain under ignored data/.

Run with backend/venv/Scripts/python.exe scripts/package_server.py
    --env-file data/server249-release/agent.env
The source may use old Agent keys or AGENT_-prefixed global keys.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'orgaiot-agent:20260928-online'
REQUIRED = ('SECRET_KEY', 'OPENAI_API_KEY', 'OPENAI_API_BASE', 'LLM_MODEL')
OPTIONAL = (
    'MAX_AGENT_ITERATIONS', 'MAX_EXECUTION_TIME', 'MAX_TOKENS',
    'MAX_TOKENS_PER_CONVERSATION', 'TEMPERATURE',
    'PLATFORM_SYSTEM_CODE', 'PLATFORM_MAP_SYS_TYPE',
)


def global_settings(source: Path) -> str:
    values = dotenv_values(source, interpolate=False)
    selected = {key: values.get('AGENT_' + key, values.get(key))
                for key in REQUIRED + OPTIONAL}
    for key in REQUIRED:
        if not selected[key] or (key == 'SECRET_KEY' and selected[key] == 'change-me-in-production'):
            raise ValueError('Missing usable ' + key + ' in source configuration')
    lines = ['# Add/update these keys in /opt/general.env; preserve existing SECRET_KEY when migrating data.']
    for key, value in selected.items():
        if not value:
            continue
        # Compose 1.29 env-file single quotes preserve literal $, # and spaces.
        if any(char in value for char in "\r\n'"):
            raise ValueError('Unsupported multiline or single-quoted value for ' + key)
        lines.append('AGENT_' + key + "='" + value + "'")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    args = parser.parse_args()
    content = global_settings(args.env_file)
    dest = ROOT / 'data' / 'agent-release'
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(['docker', 'build', '-t', IMAGE, str(ROOT / 'backend')], check=True)
    subprocess.run(['docker', 'image', 'save', '-o', str(dest / 'agent-image.tar'), IMAGE], check=True)
    shutil.copyfile(ROOT / 'deploy' / 'server' / 'compose.agent.yml', dest / 'compose.agent.yml')
    snippet = dest / 'global-env.append.txt'
    snippet.write_text(content, encoding='utf-8')
    snippet.chmod(0o600)
    print('Agent package ready:', dest)
    print('Files: agent-image.tar, compose.agent.yml, global-env.append.txt')
    print('The configuration snippet contains secrets. No database or frontend is included.')


if __name__ == '__main__':
    main()
