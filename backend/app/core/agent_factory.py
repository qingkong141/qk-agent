import json

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.prompts import DEFAULT_CAPABILITIES, DEFAULT_ROLE, build_system_prompt
from app.llm.factory import create_chat_model
from app.models.agent_config import AgentConfig
from app.tools.registry import tool_manager


def create_agent_executor(
    role: str = DEFAULT_ROLE,
    capabilities: str = DEFAULT_CAPABILITIES,
    *,
    system_prompt: str | None = None,
    tool_names: list[str] | None = None,
    streaming: bool = False,
) -> AgentExecutor:
    llm = create_chat_model(streaming=streaming)

    if tool_names is None:
        tools = tool_manager.get_all()
    else:
        tools = [t for name in tool_names if (t := tool_manager.get(name))]

    prompt_text = system_prompt or build_system_prompt(role, capabilities)
    prompt = ChatPromptTemplate.from_messages([
        ("system", prompt_text),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=settings.DEBUG,
        max_iterations=settings.MAX_AGENT_ITERATIONS,
        max_execution_time=settings.MAX_EXECUTION_TIME,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )


async def load_agent_config(db: AsyncSession, agent_id: str | None = None) -> AgentConfig | None:
    if agent_id:
        return await db.get(AgentConfig, agent_id)

    result = await db.execute(
        select(AgentConfig)
        .where(AgentConfig.is_default.is_(True))
        .order_by(AgentConfig.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _parse_tool_names(tools_json: str | None) -> list[str] | None:
    if not tools_json:
        return None
    try:
        parsed = json.loads(tools_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [name for name in parsed if isinstance(name, str)]


async def create_agent_executor_from_db(
    db: AsyncSession,
    *,
    agent_id: str | None = None,
    streaming: bool = False,
) -> AgentExecutor:
    config = await load_agent_config(db, agent_id)
    if config is None:
        return create_agent_executor(streaming=streaming)

    return create_agent_executor(
        role=config.role or DEFAULT_ROLE,
        capabilities=config.capabilities or DEFAULT_CAPABILITIES,
        system_prompt=config.system_prompt or None,
        tool_names=_parse_tool_names(config.tools),
        streaming=streaming,
    )


async def load_agent_runtime_config(
    db: AsyncSession,
    agent_id: str | None = None,
) -> tuple[str, list[str] | None]:
    config = await load_agent_config(db, agent_id)
    if config is None:
        return build_system_prompt(DEFAULT_ROLE, DEFAULT_CAPABILITIES), None
    prompt = config.system_prompt or build_system_prompt(
        config.role or DEFAULT_ROLE,
        config.capabilities or DEFAULT_CAPABILITIES,
    )
    return prompt, _parse_tool_names(config.tools)
