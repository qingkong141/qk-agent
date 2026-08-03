from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ──── 通用决策项 ────

class DecisionItem(BaseModel):
    """决策中的单条推荐/操作"""

    id: str
    title: str = ""
    status: str = "pending"  # pending | executed | skipped | failed
    score: Optional[float] = None
    reason: Optional[str] = None
    extra: Optional[dict[str, Any]] = None  # action_type 特定扩展字段


# ──── 事件 ────

class ClinicalEventRequest(BaseModel):
    """通用临床事件请求"""

    event_type: str  # diagnosis_created | vital_alert | lab_result | prescription_new
    patient_id: str
    patient_name: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    conversation_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class ClinicalEventResponse(BaseModel):
    """临床事件响应"""

    decision_id: str
    action_type: str
    status: str
    injected_to_conversation: bool = False


# ──── 决策查询 ────

class DecisionListItem(BaseModel):
    """决策列表项"""

    id: str
    action_type: str
    patient_name: Optional[str] = None
    trigger_text: str
    action_params: dict[str, Any] = Field(default_factory=dict)
    safety_level: str = "low"
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionListResponse(BaseModel):
    """决策列表响应"""

    items: list[DecisionListItem]


class ClinicalDecisionReport(BaseModel):
    """单条临床决策的事实型审计报告"""

    report_type: str
    decision_id: str
    patient_id: str
    action_type: str
    trigger: dict[str, Any]
    extracted_facts: dict[str, Any]
    risk: dict[str, Any]
    decision: dict[str, Any]
    confirmation: dict[str, Any]
    disclaimer: str


# ──── 确认/跳过/取消 ────

class ConfirmRequest(BaseModel):
    """确认执行请求"""

    selected_ids: list[str]
    comment: Optional[str] = None


class SkipRequest(BaseModel):
    """跳过请求"""

    skip_ids: list[str]


class DecisionActionResponse(BaseModel):
    """确认/跳过/取消 通用响应"""

    decision_id: str
    action_type: str
    status: str  # pending | partial | resolved | cancelled
    executed: list[dict[str, Any]] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    remaining: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
