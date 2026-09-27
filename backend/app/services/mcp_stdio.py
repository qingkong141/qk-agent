"""Only administrator-provisioned programs can be launched by MCP service records."""
import json
from pathlib import Path

from fastapi import HTTPException
from mcp import StdioServerParameters
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list, max_length=50)
    cwd: str | None = None
    secret_env: str = Field(default='', pattern=r'^$|^[A-Z][A-Z0-9_]{0,99}$')


def profiles():
    if not settings.MCP_STDIO_CONFIG_FILE: return {}
    try:
        data = json.loads(Path(settings.MCP_STDIO_CONFIG_FILE).read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or len(data) > 50: raise ValueError()
        return {key: Profile.model_validate(value) for key, value in data.items()}
    except (OSError, ValueError, ValidationError) as exc:
        raise HTTPException(503, '本地MCP服务配置文件不可用，请检查后台配置') from exc


def profile(profile_id):
    value = profiles().get(profile_id)
    if value is None: raise HTTPException(400, '本地服务尚未登记，请先配置后台 MCP_STDIO_CONFIG_FILE')
    return value


def catalog():
    return [{'id':key, 'name':p.name, 'secret_env':p.secret_env} for key,p in profiles().items()]


def parameters(profile_id, secret):
    p = profile(profile_id)
    executable = Path(p.command)
    if not executable.is_absolute() or not executable.is_file():
        raise HTTPException(400, '本地服务启动程序不存在，请检查后台登记的绝对路径')
    if p.cwd and not Path(p.cwd).is_dir(): raise HTTPException(400, '本地服务工作目录不存在')
    if p.secret_env and not secret: raise HTTPException(400, '请填写本地服务认证密钥')
    # The SDK inherits a small OS environment allowlist, not platform/API secrets.
    env = {'PYTHONIOENCODING':'utf-8'}
    if p.secret_env: env[p.secret_env] = secret
    return StdioServerParameters(command=str(executable), args=p.args, cwd=p.cwd, env=env)
