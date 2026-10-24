from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .identity import exact_matches, normalize, parse_time, scalar_fields
from .models import AuditResult, Policy
from .readers import read_csv, read_evidence

logger = logging.getLogger("traceveil.engine")


def _load_policies(path: Path) -> dict[str, Policy]:
    policies: dict[str, Policy] = {}
    for row in read_csv(path):
        category = normalize(row.get("category"))
        action = normalize(row.get("required_action")).upper()
        allowed = frozenset(normalize(item) for item in row.get("allowed_fields", "").split(";") if item.strip())
        if category and action:
            policies[category] = Policy(category, action, allowed)
    return policies


def _present(record: dict[str, Any]) -> bool | None:
    for key in ("state", "record_state", "result", "status", "account_status"):
        value = normalize(record.get(key))
        if value in {"absent", "deleted", "purged", "not_found", "missing", "404", "false"}:
            return False
        if value in {"present", "found", "active", "enabled", "true", "200"}:
            return True
    if record.get("rows") is not None:
        try:
            return int(record["rows"]) > 0
        except (TypeError, ValueError):
            pass
    if str(record.get("status_code", record.get("http_status", ""))) == "404":
        return False
    if str(record.get("status_code", record.get("http_status", ""))) == "200":
        return True
    if "profile" in record and record.get("profile") is None:
        return False
    if any(key in record for key in ("resource_id", "id", "record_id")) and not record.get("event") and not record.get("_text"):
        return True
    return None


def _is_active_flow(record: dict[str, Any], category: str) -> bool:
    enabled = normalize(record.get("enabled")) in {"true", "1", "yes", "active", "enabled"}
    return enabled and any(word in category for word in ("flow", "sync", "connector", "import", "replication"))


def _is_restore_risk(record: dict[str, Any], category: str) -> bool:
    allowed = normalize(record.get("restore_allowed")) in {"true", "1", "yes", "active"}
    return allowed and any(word in category for word in ("backup", "restore", "snapshot", "archive"))


def _field_violations(record: dict[str, Any], allowed_fields: frozenset[str], identity_values: set[str]) -> list[str]:
    violations = []
    direct_identifier_fields = {"name", "full_name", "email", "email_address", "phone", "telephone", "mobile", "email_hash", "postal_address", "street_address"}
    for field_name, value in scalar_fields(record):
        if not isinstance(value, (str, int, float)) or value is None:
            continue
        field = normalize(field_name)
        if field not in allowed_fields and (normalize(value) in identity_values or field in direct_identifier_fields):
            violations.append(field)
    return sorted(set(violations))


def _evidence_before_audit(record: dict[str, Any], audit_at: datetime | None) -> bool:
    for key in ("source_captured_at", "observed_at", "event_time", "updated_at", "captured_at"):
        timestamp = parse_time(record.get(key))
        if timestamp and audit_at and timestamp > audit_at:
            return False
    return True


def run_audit(workspace: Path, output: Path | None = None) -> dict[str, Any]:
    workspace = workspace.resolve()
    requests_path = workspace / "requests.csv"
    identities_path = workspace / "identities.csv"
    policies_path = workspace / "policies.csv"
    manifest_path = workspace / "manifest.csv"
    for path in (requests_path, identities_path, policies_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required workspace input is missing: {path.name}")

    requests = read_csv(requests_path)
    identity_rows = read_csv(identities_path)
    policies = _load_policies(policies_path)
    manifest = read_csv(manifest_path)
    evidence: list[dict[str, Any]] = []
    for item in manifest:
        evidence.extend(read_evidence(item, workspace))

    identifiers_by_subject: dict[str, list[dict[str, str]]] = defaultdict(list)
    shared_identifiers: dict[str, set[str]] = defaultdict(set)
    for row in identity_rows:
        subject = row.get("subject_id", "").strip()
        value = row.get("identifier_value", "").strip()
        kind = row.get("identifier_type", "identifier").strip()
        confidence = normalize(row.get("confidence") or "high")
        if subject and value and confidence in {"high", "verified", "authoritative"}:
            identifiers_by_subject[subject].append({
                "value": value,
                "type": kind,
                "source_ref": row.get("source_ref", "").strip(),
                "valid_from": row.get("valid_from", "").strip(),
                "valid_to": row.get("valid_to", "").strip(),
            })
            shared_identifiers[normalize(value, kind)].add(subject)
            shared_identifiers[normalize(value)].add(subject)

    results: list[dict[str, Any]] = []
    for request in requests:
        request_id = request.get("request_id", "").strip()
        subject_id = request.get("subject_id", "").strip()
        audit_text = request.get("audit_at", "")
        audit_at = parse_time(audit_text)
        authorization = normalize(request.get("authorization"))
        if authorization not in {"delete", "deletion_authorized", "authorized"}:
            results.append(AuditResult(request_id, subject_id, "NOT_APPLICABLE", audit_text, 0).to_dict())
            continue

        candidates: dict[str, tuple[str, str]] = {}
        candidates[normalize(subject_id, "subject_id")] = ("subject_id", "request.subject_id")
        if subject_id in identifiers_by_subject:
            for identity in identifiers_by_subject[subject_id]:
                start, end = parse_time(identity["valid_from"]), parse_time(identity["valid_to"])
                if start and audit_at and start > audit_at:
                    continue
                if end and audit_at and end < audit_at:
                    continue
                normalized = normalize(identity["value"], identity["type"])
                generic = normalize(identity["value"])
                if (len(shared_identifiers.get(normalized, set())) > 1
                        or len(shared_identifiers.get(generic, set())) > 1):
                    continue
                candidates[normalized] = (identity["type"], identity["source_ref"])
                candidates[generic] = (identity["type"], identity["source_ref"])
        candidate_values = set(candidates)
        matched: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        remediation: list[dict[str, Any]] = []
        verification: list[dict[str, Any]] = []
        uncertainty: list[str] = []

        for record in evidence:
            if not _evidence_before_audit(record, audit_at):
                continue
            matches = exact_matches(record, candidates)
            if not matches:
                continue
            resource_id = str(record.get("resource_id") or record.get("record_id") or record.get("id") or "unlabeled-resource")
            system = str(record.get("system_id") or record.get("system") or record.get("data_store") or "unknown")
            category = normalize(record.get("category") or record.get("data_category") or record.get("record_type"))
            state_present = _present(record)
            policy = policies.get(category)
            provenance = {
                "evidence_id": record.get("source_evidence", ""),
                "captured_at": record.get("source_captured_at", ""),
                "matched_identifiers": matches,
            }
            observed = {
                "system_id": system,
                "resource_id": resource_id,
                "category": category or "unclassified",
                "state": "PRESENT" if state_present else "ABSENT" if state_present is False else "UNKNOWN",
                "treatment": policy.action if policy else "UNMAPPED",
                "provenance": provenance,
            }
            matched.append(observed)
            if not policy:
                uncertainty.append(f"No policy maps category '{category or 'unclassified'}' for resource {resource_id}.")
                continue

            if _is_active_flow(record, category):
                issue = {"kind": "ACTIVE_RECREATION_PATH", "resource_id": resource_id, "system_id": system, "evidence_id": provenance["evidence_id"], "details": "An enabled data flow can copy or recreate subject data."}
                findings.append(issue)
                remediation.append({"action": "DISABLE_FLOW", "resource_id": resource_id, "system_id": system})
                verification.append({"check": "FLOW_DISABLED", "resource_id": resource_id, "system_id": system})
                continue
            if _is_restore_risk(record, category):
                issue = {"kind": "RESTORE_RISK", "resource_id": resource_id, "system_id": system, "evidence_id": provenance["evidence_id"], "details": "A restore path is allowed for data associated with the subject."}
                findings.append(issue)
                remediation.append({"action": "BLOCK_RESTORE", "resource_id": resource_id, "system_id": system})
                verification.append({"check": "RESTORE_BLOCKED", "resource_id": resource_id, "system_id": system})
                continue

            field_violations = _field_violations(record, policy.allowed_fields, candidate_values)
            if policy.action == "DELETE":
                if state_present is True:
                    findings.append({"kind": "DATA_REMAINS", "resource_id": resource_id, "system_id": system, "evidence_id": provenance["evidence_id"], "category": category})
                    remediation.append({"action": "DELETE_RECORD", "resource_id": resource_id, "system_id": system})
                    verification.append({"check": "RECORD_ABSENT", "resource_id": resource_id, "system_id": system})
                elif state_present is False:
                    verification.append({"check": "RECORD_ABSENT", "resource_id": resource_id, "system_id": system, **provenance})
                else:
                    uncertainty.append(f"State is unknown for {system}/{resource_id}.")
            elif policy.action in {"REDACT", "ANONYMIZE"}:
                if field_violations:
                    action = "REDACT_RECORD" if policy.action == "REDACT" else "ANONYMIZE_RECORD"
                    findings.append({"kind": "IDENTIFIERS_REMAIN", "resource_id": resource_id, "system_id": system, "evidence_id": provenance["evidence_id"], "fields": field_violations})
                    remediation.append({"action": action, "resource_id": resource_id, "system_id": system, "fields": field_violations})
                    verification.append({"check": "IDENTIFIERS_REMOVED", "resource_id": resource_id, "system_id": system, "fields": field_violations})
                else:
                    verification.append({"check": "TREATMENT_CONFIRMED", "resource_id": resource_id, "system_id": system, "treatment": policy.action, **provenance})
            elif policy.action == "RETAIN":
                if state_present:
                    if field_violations:
                        findings.append({"kind": "RETAINED_RECORD_HAS_UNALLOWED_IDENTIFIERS", "resource_id": resource_id, "system_id": system, "evidence_id": provenance["evidence_id"], "fields": field_violations})
                        remediation.append({"action": "REDACT_RECORD", "resource_id": resource_id, "system_id": system, "fields": field_violations})
                        verification.append({"check": "IDENTIFIERS_REMOVED", "resource_id": resource_id, "system_id": system, "fields": field_violations})
                    else:
                        verification.append({"check": "REQUIRED_RECORD_PRESERVED", "resource_id": resource_id, "system_id": system, **provenance})
                else:
                    uncertainty.append(f"Required retained record {system}/{resource_id} was not observed as present.")
            else:
                uncertainty.append(f"Unsupported action '{policy.action}' for category '{category}'.")

        shared = [value for value, owners in shared_identifiers.items() if subject_id in owners]
        if shared:
            uncertainty.append(f"{len(shared)} identifier value(s) are shared across subjects and were excluded from matching.")
        if not matched:
            uncertainty.append("No evidence record could be linked with high-confidence identifiers; absence of matches is not proof of deletion.")
        if findings or remediation:
            decision = "ACTION_REQUIRED"
        elif uncertainty or not verification:
            decision = "INDETERMINATE"
        else:
            decision = "CLEAR"
        results.append(AuditResult(request_id, subject_id, decision, audit_text, len(evidence), matched, findings, remediation, verification, uncertainty).to_dict())

    report = {
        "schema_version": "1.0",
        "tool": {"name": "Traceveil", "version": "0.1.0"},
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workspace": workspace.name,
        "summary": {
            "requests": len(results),
            "clear": sum(row["decision"] == "CLEAR" for row in results),
            "action_required": sum(row["decision"] == "ACTION_REQUIRED" for row in results),
            "not_applicable": sum(row["decision"] == "NOT_APPLICABLE" for row in results),
            "indeterminate": sum(row["decision"] == "INDETERMINATE" for row in results),
        },
        "results": results,
    }
    target = output or (workspace / "traceveil-report.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
