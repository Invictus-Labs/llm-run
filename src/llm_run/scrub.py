"""Scrub OAuth / API tokens from log lines and quota evidence."""

from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)(\S+)"),
    re.compile(r"(?i)(CLAUDE_CODE_OAUTH_TOKEN=)(\S+)"),
]


def scrub(text: str) -> str:
    out = text
    for pat in _PATTERNS:
        if pat.groups == 2:
            out = pat.sub(r"\1<redacted>", out)
        else:
            out = pat.sub("<redacted>", out)
    return out
