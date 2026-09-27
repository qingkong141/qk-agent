"""Per-account OpenAI-compatible connections; credentials never enter agent configs."""
import asyncio
import base64
import hashlib
import uuid
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import delete, or_, select, update

from app.api.datasets import NameInput, identity, scope
from app.api.studio import RevisionInput
from app.config import settings
from app.dependencies import CurrentUser, DbSession
from app.models.agent_model import AgentModel
from app.models.studio import StudioArtifact

router = APIRouter(prefix='/studio/agent-models', tags=['agent-models'])
PREFIX = 'custom:'


class Input(NameInput):
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=150)
    api_key: SecretStr | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip('/')
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or any(c.isspace() for c in value):
            raise ValueError('请输入不含账号、查询参数的 HTTP/HTTPS 服务地址')
        if parts.path.endswith(('/chat/completions', '/models')):
            raise ValueError('请填写 API 基础地址，不包含 /chat/completions 或 /models')
        return value

    @field_validator('model')
    @classmethod
    def valid_model(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value): raise ValueError('请填写有效的模型标识')
        return value


def cipher():
    if settings.SECRET_KEY == 'change-me-in-production': raise HTTPException(503, '请先配置后台 SECRET_KEY 后保存模型密钥')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('agent-models:'+settings.SECRET_KEY).encode()).digest()))


def credential(data, previous=''):
    key = data.api_key.get_secret_value().strip() if data.api_key else ''
    if not key:
        if previous: return previous
        raise HTTPException(400, '请填写 API Key')
    if len(key) > 2000 or any(ord(c) < 32 for c in key): raise HTTPException(400, 'API Key 格式无效')
    return cipher().encrypt(key.encode()).decode()


def public(item):
    return {'id': PREFIX+item.id, 'name': item.name, 'base_url': item.base_url, 'model': item.model,
            'provider': 'openai', 'source': '自定义', 'has_key': bool(item.credential), 'revision': item.revision}


async def owned(model_id, db, user):
    item = await db.scalar(select(AgentModel).where(AgentModel.id == model_id.removeprefix(PREFIX), *scope(AgentModel, user)))
    if not item: raise HTTPException(404, '模型配置不存在')
    return item


async def entries(db, user):
    return [public(item) for item in await db.scalars(select(AgentModel).where(*scope(AgentModel, user)).order_by(AgentModel.created_at.desc()))]


def chat_model(item):
    try: key = cipher().decrypt(item.credential.encode()).decode()
    except InvalidToken as exc: raise HTTPException(409, '模型密钥无法解密，请重新保存 API Key') from exc
    return ChatOpenAI(model=item.model, base_url=item.base_url, api_key=key, streaming=False,
                      temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS, timeout=45, max_retries=0)


@router.get('')
async def listing(db: DbSession, user: CurrentUser):
    return await entries(db, user)


@router.post('', status_code=201)
async def create(data: Input, db: DbSession, user: CurrentUser):
    scope(AgentModel, user)
    item = AgentModel(id=str(uuid.uuid4()), name=data.name, base_url=data.base_url, model=data.model,
                      credential=credential(data), **identity(user))
    db.add(item); await db.commit(); await db.refresh(item)
    return public(item)


@router.put('/{model_id}')
async def edit(model_id: str, data: Input, db: DbSession, user: CurrentUser):
    item = await owned(model_id, db, user)
    changed = await db.execute(update(AgentModel).where(AgentModel.id == item.id, AgentModel.revision == data.expected_revision,
        *scope(AgentModel, user)).values(name=data.name, base_url=data.base_url, model=data.model,
        credential=credential(data, item.credential), revision=AgentModel.revision+1))
    if changed.rowcount != 1: raise HTTPException(409, '模型配置已更新，请刷新后重试')
    await db.commit(); await db.refresh(item)
    return public(item)


@router.delete('/{model_id}')
async def remove(model_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    item = await owned(model_id, db, user)
    reference = PREFIX+item.id
    used = await db.scalar(select(StudioArtifact.id).where(StudioArtifact.kind == 'studio_agent', *scope(StudioArtifact, user),
        or_(StudioArtifact.config['model'].as_string() == reference, StudioArtifact.published_config['model'].as_string() == reference)).limit(1))
    if used: raise HTTPException(409, '模型正在被智能体草稿或发布版本使用，请先更换模型并更新发布版本或停用')
    changed = await db.execute(delete(AgentModel).where(AgentModel.id == item.id, AgentModel.revision == data.expected_revision, *scope(AgentModel, user)))
    if changed.rowcount != 1: raise HTTPException(409, '模型配置已更新，请刷新后重试')
    await db.commit()
    return {'id': reference}


@router.post('/{model_id}/test')
async def test(model_id: str, db: DbSession, user: CurrentUser):
    item = await owned(model_id, db, user)
    tool = {'type': 'function', 'function': {'name': 'connection_check', 'description': '检查工具调用能力', 'parameters': {'type': 'object', 'properties': {}}}}
    try:
        model = chat_model(item).bind_tools([tool])
        response = await asyncio.wait_for(model.ainvoke([HumanMessage(content='请调用 connection_check 检查连接。')]), timeout=50)
        if not any(call['name'] == 'connection_check' for call in response.tool_calls):
            raise HTTPException(400, '模型已响应，但未返回工具调用；请确认该模型支持 Function Calling')
    except HTTPException: raise
    except asyncio.TimeoutError as exc: raise HTTPException(504, '模型连接超时，请检查服务地址') from exc
    except Exception as exc: raise HTTPException(502, '模型测试失败，请检查服务地址、API Key、模型标识及工具调用支持') from exc
    return {'ok': True, 'message': '连接成功，已验证模型响应和工具调用'}
