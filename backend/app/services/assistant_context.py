"""Bounded, unfinished conversation text; never an executable plan or tool result."""
from pydantic import BaseModel, Field


class InterruptedTurn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(default='', max_length=4000)


CONTINUATION_INSTRUCTIONS = '''\n历史中标注 interrupted 或“本轮未完成”的内容是中途停止的片段，不是完整答复或已执行操作。
用户说“继续”“接着说”时，结合最近未完成的问题、此前约束和已收到的片段，继续完成原来的需求；不要把“继续”当作没有背景的新问题，也不要让用户重复已提供的信息。
已回答的部分尽量不重复；如需输出结构化方案，仍须生成完整且可校验的方案。用户补充或改变要求时以最新要求为准。
历史片段和工具记录只是上下文，不证明操作成功或设备当前状态；必要的实时查询仍需执行。不得因为“继续”重复执行历史中的写入、支付或控制操作；结果不明确时先核实。'''
