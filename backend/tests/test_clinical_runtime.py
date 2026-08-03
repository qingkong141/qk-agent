from datetime import datetime, timezone
from types import SimpleNamespace

from app.clinical.context_extractor import parse_context_response
from app.clinical.risk_engine import assess_infusion_risk, is_execution_blocked
from app.services.clinical_report_service import build_decision_report


def test_context_parser_preserves_explicit_facts():
    context = parse_context_response(
        '{"diagnoses":["糖尿病"],"vital_signs":{"heart_rate":"120 bpm"},'
        '"medications":[],"allergies":[],"devices":[],"symptoms":[],"missing_fields":[]}',
        patient_id="P001",
        source_text="糖尿病，心率 120 bpm",
    )

    assert context.patient_id == "P001"
    assert context.diagnoses == ["糖尿病"]
    assert context.vital_signs["heart_rate"] == "120 bpm"
    assert context.source_text == "糖尿病，心率 120 bpm"


def test_context_parser_fails_closed_on_invalid_json():
    context = parse_context_response("不是 JSON", patient_id="P001", source_text="原文")

    assert context.diagnoses == []
    assert context.missing_fields == ["clinical_context_parse_failed"]


def test_infusion_risk_blocks_when_execution_data_is_missing():
    result = assess_infusion_risk({"suggested_rate": 40})

    assert result["blocked"] is True
    assert result["level"] == "high"
    assert {item["code"] for item in result["findings"]} == {
        "DEVICE_ID_MISSING",
        "CURRENT_RATE_INVALID",
    }


def test_infusion_risk_blocks_large_rate_change():
    result = assess_infusion_risk({
        "device_id": "pump-1",
        "current_rate": 40,
        "suggested_rate": 80,
    })

    assert result["blocked"] is True
    assert result["level"] == "high"
    assert result["findings"][0]["code"] == "RATE_CHANGE_OVER_50"


def test_saved_risk_assessment_blocks_execution():
    assert is_execution_blocked({"risk_assessment": {"blocked": True}}) is True
    assert is_execution_blocked({"risk_assessment": {"blocked": False}}) is False


def test_decision_report_only_summarizes_saved_fields():
    now = datetime.now(timezone.utc)
    decision = SimpleNamespace(
        id="D001",
        patient_id="P001",
        action_type="infusion_adjust",
        trigger_source="chat",
        trigger_text="调整滴速",
        created_at=now,
        extracted_info={"device_id": "pump-1"},
        safety_level="high",
        action_params={
            "items": [{"id": "rate_adjust", "status": "pending"}],
            "risk_assessment": {"blocked": True},
        },
        status="pending",
        confirmed_by=None,
        last_confirmed_at=None,
    )

    report = build_decision_report(decision)

    assert report["decision_id"] == "D001"
    assert report["risk"]["assessment"] == {"blocked": True}
    assert report["decision"]["item_counts"] == {"pending": 1}
    assert "治疗意见" in report["disclaimer"]
