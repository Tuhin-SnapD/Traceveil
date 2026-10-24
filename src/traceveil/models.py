from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Policy:
    category: str
    action: str
    allowed_fields: frozenset[str] = frozenset()


@dataclass
class AuditResult:
    request_id: str
    subject_id: str
    decision: str
    audit_at: str
    evidence_count: int
    matched_records: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    remediation: list[dict[str, Any]] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "subject_id": self.subject_id,
            "decision": self.decision,
            "audit_at": self.audit_at,
            "evidence_count": self.evidence_count,
            "matched_records": self.matched_records,
            "findings": self.findings,
            "remediation": self.remediation,
            "verification": self.verification,
            "uncertainty": self.uncertainty,
        }
