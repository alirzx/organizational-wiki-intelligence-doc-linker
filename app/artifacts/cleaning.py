from __future__ import annotations

import re
import unicodedata

DATA_IMAGE_RE = re.compile(
    r"<img\b[^>]*?src=[\"']data:image/[^;]+;base64,[^\"']+[\"'][^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
MD_DATA_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(data:image/[^;]+;base64,[^)]+\)", flags=re.IGNORECASE | re.DOTALL
)


def clean_text(value: str) -> str:
    value = value or ""
    # Strip giant inline image payloads before Unicode normalization.
    value = DATA_IMAGE_RE.sub(" ", value)
    value = MD_DATA_IMAGE_RE.sub(" ", value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u200c", "‌")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
