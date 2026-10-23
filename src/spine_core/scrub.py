"""Secret scrubbing for anything Spine writes into a project (and so into git)."""

from __future__ import annotations

import re

PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
]
# KEY=value / key: value where the key name says secret. Keeps the name, drops the value.
ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|secret|token|passw(?:or)?d|credential)[A-Z0-9_]*)(\s*[:=]\s*)(['\"]?)([^\s'\"]{8,})\3"
)


def scrub(text: str) -> tuple[str, int]:
    """Return (text with secrets replaced by [REDACTED], number of redactions)."""
    count = 0
    for pattern in PATTERNS:
        text, n = pattern.subn("[REDACTED]", text)
        count += n
    text, n = ASSIGNMENT.subn(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return text, count + n
