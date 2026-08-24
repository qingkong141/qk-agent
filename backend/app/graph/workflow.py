from functools import lru_cache

from langgraph.graph import END, StateGraph

from app.graph.nodes import (
    calc_agent_node,
    education_agent_node,
    infusion_agent_node,
    kb_agent_node,
    response_agent_node,
    route_intent,
    supervisor_node,
)
from app.graph.state import WorkflowState

WORKFLOW_NODES = frozenset({
    "supervisor", "kb_agent", "calc_agent", "response_agent",
    "education_agent", "infusion_agent",
})


def build_workflow():
    graph = StateGraph(WorkflowState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("kb_agent", kb_agent_node)
    graph.add_node("calc_agent", calc_agent_node)
    graph.add_node("response_agent", response_agent_node)
    graph.add_node("education_agent", education_agent_node)
    graph.add_node("infusion_agent", infusion_agent_node)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_intent,
        {
            "response": "response_agent",
            "knowledge_query": "kb_agent",
            "calculation": "calc_agent",
            "general": "response_agent",
            "education_recommend": "education_agent",
            "infusion_adjust": "infusion_agent",
        },
    )
    graph.add_edge("kb_agent", "response_agent")
    graph.add_edge("calc_agent", "response_agent")
    graph.add_edge("education_agent", "response_agent")
    graph.add_edge("infusion_agent", "response_agent")
    graph.add_edge("response_agent", END)
    return graph.compile()


@lru_cache
def get_workflow():
    return build_workflow()
