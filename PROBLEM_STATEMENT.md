# Traceveil problem statement

## Mission

Traceveil audits authorized personal-data deletion requests across the systems represented in a local evidence workspace. It connects requests to trusted current and historical identifiers, reconstructs evidence at a stated point in time, applies category-specific data policies, and produces an auditable report of findings and proposed actions.

Traceveil is an investigation and planning tool. It never deletes, redacts, anonymizes, disables, or blocks resources itself.

## The problem

A deletion workflow can succeed in one service while copies remain in replicas, indexes, exports, support systems, analytics, logs, archives, or backups. Active synchronization and restore paths can recreate data. Conversely, retention policies may require some records to remain, with only a restricted set of fields.

A workflow status or generic success event is not proof of resource state. Conclusions must be tied to source evidence and the audit cutoff. Missing evidence must produce uncertainty rather than an unsupported claim that deletion is complete.

## Inputs

A workspace contains four control files and a set of local evidence files:

```text
workspace/
├── requests.csv
├── identities.csv
├── policies.csv
├── manifest.csv
└── evidence/
```

### Request table

`requests.csv` uses these columns:

```text
request_id,subject_id,requested_at,audit_at,authorization,request_text
```

`authorization` must explicitly indicate deletion (`DELETE`, `DELETION_AUTHORIZED`, or `AUTHORIZED`). Other values are treated as not authorizing deletion. Times use ISO 8601.

### Identity history

`identities.csv` uses:

```text
subject_id,identifier_type,identifier_value,valid_from,valid_to,confidence,source_ref
```

Only high-confidence identity assertions (`high`, `verified`, or `authoritative`) are used for automatic matching. Optional validity timestamps bound when an identifier belongs to a subject. Shared identifiers are excluded from automatic matching.

### Policy table

`policies.csv` uses:

```text
category,required_action,allowed_fields
```

`required_action` is one of `DELETE`, `REDACT`, `ANONYMIZE`, or `RETAIN`. `allowed_fields` is a semicolon-separated list. A record's category must have a policy mapping before Traceveil can claim a clear result.

### Evidence manifest

`manifest.csv` uses:

```text
evidence_id,path,system_id,captured_at
```

Evidence paths are relative to the workspace and must remain inside it. Evidence captured after `audit_at`, or containing a later observed/event/update timestamp, is excluded from the request's decision.

Evidence records should contain a `resource_id` (or `id`), category, subject identifiers, and a state/result where available. Optional fields such as `enabled`, `restore_allowed`, `observed_at`, and `event_time` support state and recreation-risk analysis.

## Decisions

Each authorized request receives one decision:

- `CLEAR`: linked evidence supports the configured treatment for observed records, with no discovered remediation requirement.
- `ACTION_REQUIRED`: evidence establishes a policy violation or active recreation/restore risk.
- `INDETERMINATE`: evidence is missing, ambiguous, has unknown state, or lacks a policy mapping.
- `NOT_APPLICABLE`: deletion was not explicitly authorized.

`CLEAR` is scoped to supplied evidence; it does not claim that unlisted systems were searched. No evidence match is not proof of absence.

## Report

The CLI writes a UTF-8 JSON report containing tool/schema metadata, an aggregate decision count, and one result per request. Each result includes:

- request, subject, and audit timestamp;
- matched records and identifier provenance;
- source evidence IDs and capture times;
- policy treatment and observed state;
- evidence-supported findings;
- proposed remediation and verification checks; and
- uncertainty notes.

Findings cite source evidence. Proposed actions are clearly labeled and are never represented as completed work.

## Supported evidence

The built-in readers handle CSV, TSV, JSON, JSONL/NDJSON, SQLite-compatible SQL, text, key-value logs, and newline-delimited JSON logs. PDF extraction (`pypdf`) and image OCR (`Pillow`, `pytesseract`, plus the Tesseract executable) are optional. If an optional extractor is unavailable or fails, Traceveil emits a warning and continues with other evidence.

## Safety constraints

- Use exact identifier matching; do not infer identity from fuzzy names, request order, filenames, or resource-number patterns.
- Keep provenance for identity links and evidence observations.
- Treat status claims as claims unless a resource state is observed.
- Preserve required records and only permit fields listed in policy.
- Keep evidence and generated reports local; workspaces may contain personal data.
- Keep the baseline deterministic and network-free.
