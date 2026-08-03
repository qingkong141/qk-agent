from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.config import settings
from app.core.prompts import DEFAULT_CAPABILITIES, DEFAULT_ROLE, build_system_prompt
from app.llm.factory import create_chat_model
from app.tools.registry import tool_manager


def create_agent_executor(
    role: str = DEFAULT_ROLE,
    capabilities: str = DEFAULT_CAPABILITIES,
    *,
    tool_names: list[str] | None = None,
    streaming: bool = False,
) -> AgentExecutor:
    llm = create_chat_model(streaming=streaming)

    if tool_names is None:
        tools = tool_manager.get_all()
    else:
        tools = [t for name in tool_names if (t := tool_manager.get(name))]

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt(role, capabilities)),
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
