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
# Only a standalone, immediately adjacent provider retry sentence may extend
# a plain quota line. Do not collect arbitrary following task output.
_RETRY_LINE = re.compile(
    r"Try again (?:in|after) [0-9]{1,9}(?:\.[0-9]{1,3})? (?:days?|hours?|minutes?)[.!]?",
    re.I,
)


def _is_limit_message(message: str) -> bool:
    lines = message.strip().split("\n")
    return bool(
        _LIMIT.fullmatch(message.strip())
        or (
            len(lines) == 2
            and _LIMIT.fullmatch(lines[0].strip())
            and _RETRY_LINE.fullmatch(lines[1].strip())
        )
    )


_HOURS = re.compile(r"(?:in|after)\s+(\d+(?:\.\d+)?)\s*hours?", re.I)
_MINUTES = re.compile(r"(?:in|after)\s+(\d+(?:\.\d+)?)\s*minutes?", re.I)
_DAYS = re.compile(r"(?:in|after)\s+(\d+(?:\.\d+)?)\s*days?", re.I)


def _window(output: str) -> str:
    return (
        output
        if len(output) <= 2 * _SCAN_CAP
        else output[:_SCAN_CAP] + "\n" + output[-_SCAN_CAP:]
    )


def _records(output: str):
    # Decode complete records before scanning onward. Truncating a JSON envelope
    # or reparsing its nested lines could turn task content into a retry signal.
    decoder = json.JSONDecoder()
    start = re.compile(r"^[ \t]*(?:ERROR: )?([\[{])", re.M)
    cursor = 0
    while match := start.search(output, cursor):
        if match.start() > cursor:
            yield output[cursor : match.start()]
        try:
            obj, end = decoder.raw_decode(output, match.start(1))
        except (ValueError, RecursionError):
            # An incomplete/malformed record may contain nested error examples.
            # Its boundaries are unknown: fail closed instead of replaying work.
            return
        if isinstance(obj, dict):
            yield obj
        cursor = end
    if cursor < len(output):
        yield output[cursor:]


def _objects(output: str):
    for record in _records(output):
        if isinstance(record, dict):
            yield record


def classify_output(output: str, model: str | None = None) -> str | None:
    """Call only for nonzero child exits, never for timeout or interrupt."""
    for obj in _records(output):
        if isinstance(obj, str):
            for line in obj.split("\n"):
                if _LIMIT.fullmatch(line.strip()):
                    return "quota"
                if _AUTH.fullmatch(line.strip()):
                    return "auth"
            continue
        if obj.get("type") == "llm_run_error":
            kind = obj.get("kind")
            if kind in ("quota", "auth", "rejected_model"):
                return kind
        if obj.get("type") in ("error", "turn.failed"):
            detail = obj.get("error") if obj.get("type") == "turn.failed" else obj
            message = detail.get("message") if isinstance(detail, dict) else None
            if isinstance(message, str):
                if _is_limit_message(message):
                    return "quota"
                if _AUTH.fullmatch(message.strip()):
                    return "auth"
        if obj.get("type") not in (None, "error", "turn.failed"):
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
                and 0 < seconds <= 604800
                and math.isfinite(seconds)
            ):
                return int(now_s + seconds)
    # Reset hints belong to a recognized provider quota message, never to
    # task prose elsewhere in the output or inside a completed result.
    for record in _records(output):
        messages = []
        if isinstance(record, str):
            lines = record.split("\n")
            for index, line in enumerate(lines):
                if _LIMIT.fullmatch(line.strip()):
                    message = line.strip()
                    if index + 1 < len(lines) and _RETRY_LINE.fullmatch(
                        lines[index + 1].strip()
                    ):
                        message += " " + lines[index + 1].strip()
                    messages.append(message)
        elif record.get("type") in (None, "error", "turn.failed"):
            detail = (
                record.get("error") if record.get("type") == "turn.failed" else record
            )
            message = detail.get("message") if isinstance(detail, dict) else None
            if isinstance(message, str) and _is_limit_message(message):
                messages.append(message)
            error = record.get("error")
            if (
                isinstance(error, dict)
                and (
                    _TYPES.get(str(error.get("type"))) == "quota"
                    or _TYPES.get(str(error.get("code"))) == "quota"
                )
                and isinstance(error.get("message"), str)
            ):
                messages.append(error["message"])
        for message in messages:
            matches = [
                (match.start(), match, multiplier)
                for pattern, multiplier in (
                    (_DAYS, 86400),
                    (_HOURS, 3600),
                    (_MINUTES, 60),
                )
                if (match := pattern.search(message))
            ]
            if matches:
                _, match, multiplier = min(matches, key=lambda item: item[0])
                seconds = float(match.group(1)) * multiplier
                if 0 < seconds <= 604800:
                    return int(now_s + seconds)
    return int(now_s + default_cooldown_s)
