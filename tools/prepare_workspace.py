#!/usr/bin/env python3
"""Convert the bundled example corpus into a Traceveil workspace."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "sample-corpus"
DEFAULT_OUTPUT = ROOT / "build" / "sample-corpus-workspace"

DELETE_TERMS = re.compile(
    r"\b(delete|deletion|erase|erasure|remove|removal|purge|close my account|"
    r"close the account|right to be forgotten|eliminate my data|"
    r"borrar|eliminar|suprimir|effacer|supprimer|löschen|entfernen|"
    r"cancellare|eliminare|apagar|excluir|удалить|删除|刪除|消去)\b",
    re.IGNORECASE,
)
NON_DELETE_TERMS = re.compile(
    r"\b(access|copy of my data|export|correct|correction|rectify|update my|"
    r"opt.?out|stop marketing|unsubscribe|portability|download my data|"
    r"acceso|corregir|exportar|zugriff|korrigieren|accès|corriger)\b",
    re.IGNORECASE,
)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, fields: list[str], items: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Path to the bundled raw corpus")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination for the prepared workspace")
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.output.resolve()
    if destination.exists():
        parser.error(f"destination already exists; remove it or choose another path: {destination}")
    required = ["requests.csv", "identities.csv", "identity_history.csv", "policies.csv", "manifest.csv", "evidence"]
    missing = [name for name in required if not (source / name).exists()]
    if missing:
        parser.error(f"source corpus is missing: {', '.join(missing)}")

    current = rows(source / "identities.csv")
    history = rows(source / "identity_history.csv")
    by_email: dict[str, set[str]] = {}
    for row in current:
        if row.get("email") and row.get("user_id"):
            by_email.setdefault(row["email"].strip().casefold(), set()).add(row["user_id"].strip())
    for row in history:
        if row.get("field_name", "").casefold() in {"email", "email_address"}:
            for value in (row.get("old_value", ""), row.get("new_value", "")):
                if value and row.get("user_id"):
                    by_email.setdefault(value.strip().casefold(), set()).add(row["user_id"].strip())

    requests: list[dict[str, str]] = []
    for row in rows(source / "requests.csv"):
        text = row.get("request_text", "")
        authorized = bool(DELETE_TERMS.search(text)) and not bool(NON_DELETE_TERMS.search(text))
        subjects = by_email.get(row.get("email", "").strip().casefold(), set())
        requests.append({
            "request_id": row.get("case_id", ""),
            "subject_id": next(iter(subjects)) if len(subjects) == 1 else "",
            "requested_at": row.get("requested_at", ""),
            "audit_at": row.get("audit_at", ""),
            "authorization": "DELETE" if authorized else "NO",
            "request_text": text,
        })

    identities: list[dict[str, str]] = []
    for row in current:
        user = row.get("user_id", "")
        for kind, key in (("name", "name"), ("email", "email"), ("phone", "phone"), ("email_hash", "email_hash")):
            if row.get(key):
                identities.append({"subject_id": user, "identifier_type": kind, "identifier_value": row[key],
                                   "valid_from": "", "valid_to": "", "confidence": "high", "source_ref": "identities.csv"})
    for row in history:
        kind = row.get("field_name", "").strip().casefold()
        if kind in {"email_address", "email_hash"}:
            kind = "email" if kind == "email_address" else kind
        if kind not in {"email", "phone", "email_hash", "name"}:
            continue
        event_time = row.get("event_time", "")
        if row.get("old_value"):
            identities.append({"subject_id": row.get("user_id", ""), "identifier_type": kind,
                               "identifier_value": row["old_value"], "valid_from": "", "valid_to": event_time,
                               "confidence": "high", "source_ref": row.get("audit_id", "identity_history.csv")})
        if row.get("new_value"):
            identities.append({"subject_id": row.get("user_id", ""), "identifier_type": kind,
                               "identifier_value": row["new_value"], "valid_from": event_time, "valid_to": "",
                               "confidence": "high", "source_ref": row.get("audit_id", "identity_history.csv")})

    policies = []
    for row in rows(source / "policies.csv"):
        action = row.get("required_action", "").strip().upper()
        # The sample's generic policy schema does not enumerate allowed fields.
        # Preserve record keys and stable subject references; the engine still
        # flags direct identifiers such as names, emails, phones, and hashes.
        allowed = "resource_id;record_id;id;subject_id;user_id;account_id;system_id;category;created_at;updated_at;event_time;timestamp;order_id"
        policies.append({"category": row.get("data_category", ""), "required_action": action, "allowed_fields": allowed})

    destination.mkdir(parents=True)
    (destination / "evidence").mkdir()
    shutil.copytree(source / "evidence", destination / "evidence", dirs_exist_ok=True)
    shutil.copy2(source / "activity.csv", destination / "activity.csv")
    write_rows(destination / "requests.csv", ["request_id", "subject_id", "requested_at", "audit_at", "authorization", "request_text"], requests)
    write_rows(destination / "identities.csv", ["subject_id", "identifier_type", "identifier_value", "valid_from", "valid_to", "confidence", "source_ref"], identities)
    write_rows(destination / "policies.csv", ["category", "required_action", "allowed_fields"], policies)

    manifest = []
    for row in rows(source / "manifest.csv"):
        path = row.get("file_path", "")
        if (destination / path).is_file():
            manifest.append({"evidence_id": row.get("file_id", ""), "path": path,
                             "system_id": row.get("origin", ""), "captured_at": row.get("captured_at", "")})
    manifest.append({"evidence_id": "activity-log", "path": "activity.csv", "system_id": "activity-log", "captured_at": ""})
    write_rows(destination / "manifest.csv", ["evidence_id", "path", "system_id", "captured_at"], manifest)
    print(f"Prepared {len(requests)} requests, {len(identities)} identity assertions, and {len(manifest)} evidence sources at {destination}")
    print("Authorization is inferred from request text; review it before using the corpus as an operational decision source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
