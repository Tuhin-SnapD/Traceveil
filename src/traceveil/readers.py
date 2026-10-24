from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("traceveil.ingest")


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def _json_records(value: Any, context: dict[str, Any] | None = None) -> Iterable[dict[str, Any]]:
    context = context or {}
    if isinstance(value, dict):
        inherited = dict(context)
        for key in ("system", "system_id", "data_store", "category", "data_category", "captured_at", "observed_at"):
            if key in value:
                inherited[key] = value[key]
        if value.get("resource_id") or value.get("id"):
            yield {**inherited, **value}
        for child in value.values():
            yield from _json_records(child, inherited)
    elif isinstance(value, list):
        for child in value:
            yield from _json_records(child, context)


def _decode_json_columns(record: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(record)
    for key, value in list(record.items()):
        if not isinstance(value, str) or not value.lstrip().startswith(("{", "[")):
            continue
        try:
            decoded[key] = json.loads(value)
        except json.JSONDecodeError:
            continue
    return decoded


def _parse_log_line(line: str) -> dict[str, Any]:
    record: dict[str, Any] = {"_text": line.strip()}
    timestamp = re.match(r"^(\d{4}-\d\d-\d\dT[^\s]+)", line.strip())
    if timestamp:
        record["event_time"] = timestamp.group(1)
    payload = re.search(r"\s(\{.*\})\s*$", line.strip())
    if payload:
        try:
            obj = json.loads(payload.group(1))
            if isinstance(obj, dict):
                record.update(obj)
                if obj.get("subject_ref") and not obj.get("subject_id"):
                    record["subject_id"] = obj["subject_ref"]
                return record
        except json.JSONDecodeError:
            pass
    for match in re.finditer(r'([A-Za-z_][\w.-]*)=(?:"([^"]*)"|\'([^\']*)\'|([^\s]+))', line):
        key = match.group(1)
        value = next((part for part in match.groups()[1:] if part is not None), "")
        if value.casefold() == "null":
            parsed: Any = None
        elif value.casefold() in {"true", "false"}:
            parsed = value.casefold() == "true"
        else:
            parsed = value
        record[key] = parsed
    if record.get("subject_ref") and not record.get("subject_id"):
        record["subject_id"] = record["subject_ref"]
    return record


def _read_sql(path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(path.read_text(encoding="utf-8-sig", errors="replace"))
        output: list[dict[str, Any]] = []
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table,) in tables:
            cursor = connection.execute(f'SELECT * FROM "{table.replace(chr(34), chr(34)*2)}"')
            names = [column[0] for column in cursor.description or ()]
            for row in cursor:
                record = _decode_json_columns(dict(zip(names, row)))
                record.setdefault("category", record.get("data_category") or table)
                output.append(record)
        return output
    finally:
        connection.close()


def read_evidence(manifest: dict[str, str], workspace: Path) -> list[dict[str, Any]]:
    evidence_id = manifest["evidence_id"]
    relative_path = Path(manifest["path"])
    path = (workspace / relative_path).resolve()
    workspace_resolved = workspace.resolve()
    if workspace_resolved not in path.parents and path != workspace_resolved:
        logger.warning("Skipping evidence path outside workspace: %s", relative_path)
        return []
    if not path.is_file():
        logger.warning("Evidence file is missing: %s", relative_path)
        return []
    suffix = path.suffix.casefold()
    records: list[dict[str, Any]] = []
    try:
        if suffix == ".csv":
            records = read_csv(path)
        elif suffix == ".tsv":
            records = read_csv(path, "\t")
        elif suffix in {".json", ".jsonl", ".ndjson"}:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if suffix in {".jsonl", ".ndjson"}:
                for line in text.splitlines():
                    if line.strip():
                        try:
                            records.extend(_json_records(json.loads(line)))
                        except json.JSONDecodeError:
                            records.append(_parse_log_line(line))
            else:
                records = list(_json_records(json.loads(text)))
        elif suffix == ".sql":
            records = _read_sql(path)
        elif suffix in {".txt", ".log"}:
            for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if line.strip():
                    records.append(_parse_log_line(line))
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
                text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
                records = [{"_text": line} for line in text.splitlines() if line.strip()]
            except Exception as exc:
                logger.warning("PDF text extraction unavailable for %s: %s", relative_path, exc)
        elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            try:
                from PIL import Image
                import pytesseract
                text = pytesseract.image_to_string(Image.open(path))
                records = [{"_text": line} for line in text.splitlines() if line.strip()]
            except Exception as exc:
                logger.warning("Image OCR unavailable for %s: %s", relative_path, exc)
        else:
            logger.warning("Unsupported evidence format %s: %s", suffix, relative_path)
    except Exception as exc:
        logger.warning("Could not parse evidence %s: %s", relative_path, exc)
    enriched = []
    for record in records:
        row = _decode_json_columns(record)
        row.setdefault("system_id", manifest.get("system_id", "unknown"))
        row.setdefault("source_evidence", evidence_id)
        row.setdefault("source_captured_at", manifest.get("captured_at", ""))
        enriched.append(row)
    return enriched
