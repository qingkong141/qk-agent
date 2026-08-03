"""不依赖 LLM 的临床风险规则引擎。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "unacceptable": 3}


@dataclass(frozen=True)
class RiskFinding:
    code: str
    level: str
    message: str
    blocking: bool = False


def _max_level(findings: list[RiskFinding], default: str) -> str:
    return max((f.level for f in findings), key=RISK_ORDER.get, default=default)


def assess_infusion_risk(params: dict[str, Any]) -> dict[str, Any]:
    findings: list[RiskFinding] = []
    current = params.get("current_rate")
    suggested = params.get("suggested_rate")
    device_id = params.get("device_id")

    if not device_id:
        findings.append(RiskFinding("DEVICE_ID_MISSING", "high", "缺少输液设备标识，禁止执行", True))
    if not isinstance(current, (int, float)) or current <= 0:
        findings.append(RiskFinding("CURRENT_RATE_INVALID", "high", "当前滴速缺失或无效，禁止执行", True))
    if not isinstance(suggested, (int, float)) or suggested <= 0:
        findings.append(RiskFinding("TARGET_RATE_INVALID", "unacceptable", "目标滴速必须大于 0，禁止执行", True))

    if isinstance(current, (int, float)) and current > 0 and isinstance(suggested, (int, float)) and suggested > 0:
        change_pct = abs(suggested - current) / current
        if change_pct > 0.5:
            findings.append(RiskFinding("RATE_CHANGE_OVER_50", "high", f"滴速调整幅度 {change_pct:.0%} 超过 50%", True))
        elif change_pct > 0.3:
            findings.append(RiskFinding("RATE_CHANGE_OVER_30", "medium", f"滴速调整幅度 {change_pct:.0%} 超过 30%"))

    level = _max_level(findings, "medium")
    return {
        "level": level,
        "blocked": any(f.blocking for f in findings),
        "findings": [asdict(f) for f in findings],
        "warnings": [f.message for f in findings],
        "engine": "deterministic-v1",
    }


def is_execution_blocked(action_params: dict[str, Any] | None) -> bool:
    return bool((action_params or {}).get("risk_assessment", {}).get("blocked"))
