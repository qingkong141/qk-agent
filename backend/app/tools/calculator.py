from langchain_core.tools import tool


@tool
def calculator(expression: str) -> str:
    """执行数学计算。输入数学表达式，如 '2 + 3 * 4'。"""
    allowed = set("0123456789+-*/().% ")
    if not all(c in allowed for c in expression):
        return "表达式包含不允许的字符"
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"


calculator_tool = calculator
