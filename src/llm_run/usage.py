"""Parse token counters emitted by coding-agent CLIs."""

import json
from typing import Any


def parse_tokens(output: str) -> tuple[dict[str, int] | None, bool]:
    """Extract usage from Claude JSON envelope or compatible usage records."""
    text = output.strip()
    candidates: list[dict[str, Any]] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            candidates.append(parsed)
        elif isinstance(parsed, list):
            candidates.extend(x for x in parsed if isinstance(x, dict))
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                candidates.append(obj)
    for obj in candidates:
        usage = obj.get("usage") or obj.get("token_usage")
        if not isinstance(usage, dict):
            continue
        inn = usage.get("input_tokens", usage.get("inputTokens"))
        out = usage.get("output_tokens", usage.get("outputTokens"))
        if inn is None and out is None:
            continue
        try:
            return {"in": int(inn or 0), "out": int(out or 0)}, False
        except (TypeError, ValueError):
            continue
    # JSON-looking output that didn't yield usage → parse_failed only if it
    # was supposed to be structured and we got garbage (non-empty, not JSON).
    if text and not candidates:
        return None, True
    return None, False
