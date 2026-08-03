from app.tools.registry import tool_manager
from app.tools.calculator import calculator_tool
from app.tools.education_tools import confirm_education_push, recommend_education, skip_education_items
from app.tools.infusion_tools import adjust_infusion_rate, query_infusion_status
from app.tools.knowledge_search import search_knowledge_base_tool
from app.tools.memory_tools import recall_memory_tool, save_memory_tool


def register_default_tools():
    tool_manager.register(calculator_tool)
    tool_manager.register(search_knowledge_base_tool)
    tool_manager.register(save_memory_tool)
    tool_manager.register(recall_memory_tool)
    # 临床决策工具
    tool_manager.register(recommend_education)
    tool_manager.register(confirm_education_push)
    tool_manager.register(skip_education_items)
    tool_manager.register(query_infusion_status)
    tool_manager.register(adjust_infusion_rate)

