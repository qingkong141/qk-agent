"""把临床自由文本转换为场景 Agent 可复用的结构化上下文。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.llm.factory import create_chat_model


class ClinicalContext(BaseModel):
    patient_id: str
    diagnoses: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    vital_signs: dict[str, Any] = Field(default_factory=dict)
    medications: list[dict[str, Any]] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    devices: list[dict[str, Any]] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_text: str


EXTRACTION_PROMPT = """你是临床文本结构化助手，只提取原文明确出现的事实，不推断、不诊断、不补全。
输出严格 JSON：
{
  "diagnoses": [], "symptoms": [], "vital_signs": {},
  "medications": [], "allergies": [], "devices": [], "missing_fields": []
}
数值必须保留单位；未出现的信息使用空数组或空对象。missing_fields 记录当前文本中明确缺少、但执行操作所需的字段。"""


def parse_context_response(content: str, *, patient_id: str, source_text: str) -> ClinicalContext:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise ValueError("提取结果不是 JSON 对象")
    except (json.JSONDecodeError, ValueError):
        payload = {"missing_fields": ["clinical_context_parse_failed"]}
    return ClinicalContext(patient_id=patient_id, source_text=source_text, **payload)


async def extract_clinical_context(text: str, patient_id: str) -> ClinicalContext:
    try:
        llm = create_chat_model()
        response = await llm.ainvoke([
            HumanMessage(content=f"{EXTRACTION_PROMPT}\n\n患者ID：{patient_id}\n原始文本：{text}")
        ])
        content = response.content if isinstance(response.content, str) else str(response.content)
        return parse_context_response(content, patient_id=patient_id, source_text=text)
    except Exception:
        return ClinicalContext(
            patient_id=patient_id,
            source_text=text,
            missing_fields=["clinical_context_extraction_failed"],
        )
