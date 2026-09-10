"""Conservative provider-error classification. Ordinary task prose is not a signal."""

import json
import math
import re
import time

_SCAN_CAP = 8192
_TYPES = {
    "rate_limit_error": "quota",
    "rate_limit_exceeded": "quota",
    "insufficient_quota": "quota",
    "quota_exceeded": "quota",
    "authentication_error": "auth",
    "invalid_api_key": "auth",
    "unauthorized": "auth",
    "model_not_found": "rejected_model",
}
_REJECTED = [
    re.compile(r"\bmodel\b.{0,160}\bnot (?:found|supported|available)\b", re.I),
    re.compile(r"\bunknown model\b", re.I),
]
_LIMIT = re.compile(
    r"^(?:ERROR: )?(?:You've hit your (?:usage )?limit|You have exceeded your usage limit)(?:[ .!·—:].*)?$",
    re.I,
)
_AUTH = re.compile(
    r"^(?:ERROR: )?(?:Not logged in[. ]*Please run /login|OAuth session expired[. ]*Please run /login)[.!]?$",
    re.I,
)
_HOURS = re.compile(r"(?:in|after)\s+(\d+(?:\.\d+)?)\s*hours?", re.I)
_MINUTES = re.compile(r"(?:in|after)\s+(\d+)\s*minutes?", re.I)
_DAYS = re.compile(r"(?:in|after)\s+(\d+)\s*days?", re.I)


def _window(output: str) -> str:
    return (
        output
        if len(output) <= 2 * _SCAN_CAP
        else output[:_SCAN_CAP] + "\n" + output[-_SCAN_CAP:]
    )


def _objects(output: str):
    # Whole JSON and JSONL both occur in CLI output. Only top-level errors count;
    # errors quoted inside a successful result or tool message are ignored.
    text = _window(output)
    for value in [text, *text.splitlines()]:
        value = value.strip().removeprefix("ERROR: ")
        try:
            obj = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(obj, dict):
            yield obj


def classify_output(output: str, model: str | None = None) -> str | None:
    """Call only for nonzero child exits, never for timeout or interrupt."""
    for obj in _objects(output):
        if obj.get("type") == "llm_run_error":
            kind = obj.get("kind")
            if kind in ("quota", "auth", "rejected_model"):
                return kind
        if obj.get("type") not in (None, "error"):
            continue
        error = obj.get("error")
        if not isinstance(error, dict):
            continue
        kind = _TYPES.get(str(error.get("type"))) or _TYPES.get(str(error.get("code")))
        if kind in ("quota", "auth"):
            return kind
        message = error.get("message", "")
        if not isinstance(message, str):
            continue
        if model and model.lower() not in message.lower():
            continue
        if kind == "rejected_model":
            return kind
        if error.get("type") in ("invalid_request_error", "not_found_error"):
            if any(pattern.search(message) for pattern in _REJECTED):
                return "rejected_model"
    for line in _window(output).splitlines():
        if _LIMIT.fullmatch(line.strip()):
            return "quota"
        if _AUTH.fullmatch(line.strip()):
            return "auth"
    return None


def parse_reset_epoch(
    output: str, default_cooldown_s: int, now: float | None = None
) -> int:
    now_s = time.time() if now is None else now
    for obj in _objects(output):
        if obj.get("type") == "llm_run_error" and obj.get("kind") == "quota":
            seconds = obj.get("retry_after_seconds")
            if (
                type(seconds) in (float, int)
                and math.isfinite(seconds)
                and 0 < seconds <= 604800
            ):
                return int(now_s + seconds)
    for pattern, multiplier in ((_DAYS, 86400), (_HOURS, 3600), (_MINUTES, 60)):
        match = pattern.search(_window(output))
        if match:
            seconds = float(match.group(1)) * multiplier
            if 0 < seconds <= 604800:
                return int(now_s + seconds)
    return int(now_s + default_cooldown_s)
