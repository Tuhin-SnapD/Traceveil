from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Iterable


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed
    except (TypeError, ValueError):
        return None


def normalize(value: Any, identifier_type: str = "") -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    kind = identifier_type.casefold()
    if kind in {"phone", "telephone", "mobile"}:
        return "".join(character for character in text if character.isdigit() or character == "+")
    if kind in {"email", "email_address"}:
        return text.replace(" ", "")
    if kind in {"hash", "email_hash", "subject_id", "account_id"}:
        return re.sub(r"\s+", "", text)
    return text


def scalar_fields(value: Any, field_name: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            yield from scalar_fields(item, str(key))
    elif isinstance(value, list):
        for item in value:
            yield from scalar_fields(item, field_name)
    else:
        yield field_name, value


def exact_matches(record: dict[str, Any], identifiers: dict[str, tuple[str, str]]) -> list[dict[str, str]]:
    field_types = {
        "email": "email", "email_address": "email", "phone": "phone", "telephone": "phone",
        "mobile": "phone", "email_hash": "email_hash", "subject_id": "subject_id",
        "customer_id": "subject_id", "account_id": "account_id",
    }
    matches = []
    for field_name, value in scalar_fields(record):
        if not isinstance(value, (str, int, float)) or value is None:
            continue
        kind = field_types.get(field_name.casefold(), "")
        normalized = normalize(value, kind)
        identity = identifiers.get(normalized) or identifiers.get(normalize(value))
        if identity:
            identifier_type, source_ref = identity
            matches.append({"field": field_name, "identifier_type": identifier_type, "source_ref": source_ref})
    text = normalize(record.get("_text", ""))
    for token, identity in identifiers.items():
        if len(token) < 5 or not text:
            continue
        if re.search(r"(?<![A-Za-z0-9@._+-])" + re.escape(token) + r"(?![A-Za-z0-9@._+-])", text):
            identifier_type, source_ref = identity
            matches.append({"field": "document_text", "identifier_type": identifier_type, "source_ref": source_ref})
    return matches


def contains_identifier_text(value: Any, identifiers: set[str]) -> bool:
    text = normalize(value)
    for token in identifiers:
        if len(token) < 5:
            continue
        if re.search(r"(?<![A-Za-z0-9@._+-])" + re.escape(token) + r"(?![A-Za-z0-9@._+-])", text):
            return True
    return False
