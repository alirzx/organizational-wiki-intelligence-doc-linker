from __future__ import annotations

import re
import unicodedata

DATA_IMAGE_RE = re.compile(
    r"<img\b(?=[^>]*\bdata:image/[^>]*;base64,)[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
MD_DATA_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*data:image/[^;\s]+;base64,[^)]+\)",
    flags=re.IGNORECASE | re.DOTALL,
)
BARE_DATA_IMAGE_RE = re.compile(
    r"data:image/[a-z0-9.+-]+(?:;[a-z0-9.+-]+=[^;,\s]*)*;base64,"
    r"[a-z0-9+/=\r\n]+",
    flags=re.IGNORECASE,
)


def clean_text(value: str) -> str:
    value = value or ""
    # Strip giant inline image payloads before Unicode normalization.
    value = DATA_IMAGE_RE.sub(" ", value)
    value = MD_DATA_IMAGE_RE.sub(" ", value)
    value = BARE_DATA_IMAGE_RE.sub(" ", value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u200c", "‌")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
