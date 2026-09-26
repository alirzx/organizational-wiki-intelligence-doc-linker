from __future__ import annotations

import hashlib
import re
import uuid

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
NAMESPACE = uuid.UUID("7439a460-48c0-4bb2-8f19-f9f541d17f30")


def validate_safe_id(value: str, label: str = "id") -> str:
    if not SAFE_ID_RE.fullmatch(value or "") or ".." in value:
        raise ValueError(f"{label} must be a safe 1-192 character identifier")
    return value


def stable_point_id(*parts: object) -> str:
    canonical = "|".join(str(part) for part in parts)
    return str(uuid.uuid5(NAMESPACE, canonical))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
