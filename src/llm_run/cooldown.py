"""Local cooldown cache, merged under a file lock for concurrent runners."""

import fcntl
import json
import math
import os
from pathlib import Path
import time

from llm_run.paths import quota_path


def _invalid_number(_value):
    raise ValueError("non-finite cache number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite cache number")
    return number


def _bounded_int(value):
    number = int(value)
    if abs(number) > 2**63 - 1:
        raise ValueError("cache number out of range")
    return number


class Cooldowns:
    def __init__(self, path: Path | None = None):
        self.path = path or quota_path()
        self.warning: str | None = None

    def _decode(self, text: str) -> dict:
        try:
            data = (
                json.loads(
                    text,
                    parse_constant=_invalid_number,
                    parse_float=_finite_float,
                    parse_int=_bounded_int,
                )
                if text.strip()
                else {}
            )
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError, RecursionError):
            self.warning = "cooldown cache is invalid; unknown cooldowns are ignored"
            return {}

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        with self._open() as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            return self._decode(fh.read())

    def _open(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, "r+", encoding="utf-8", errors="replace")

    def get(self, engine: str) -> dict:
        entry = self.read().get(engine, {})
        return entry if isinstance(entry, dict) else {}

    def active(self, engine: str) -> bool:
        until = self.get(engine).get("until")
        return (
            isinstance(until, (int, float))
            and not isinstance(until, bool)
            and math.isfinite(until)
            and until > time.time()
        )

    def record(
        self,
        engine: str,
        until: int | None,
        reason: str,
        *,
        unless_newer_than: float | None = None,
    ) -> None:
        if reason not in {"quota", "ok", "auth", "rejected_model"}:
            raise ValueError("invalid cooldown reason")
        with self._open() as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data = self._decode(fh.read())
            previous = data.get(engine)
            checked = previous.get("checked_at") if isinstance(previous, dict) else None
            if (
                unless_newer_than is not None
                and isinstance(checked, (int, float))
                and checked > unless_newer_than
            ):
                return
            data[engine] = {"until": until, "reason": reason, "checked_at": time.time()}
            encoded = json.dumps(data, allow_nan=False)
            fh.seek(0)
            fh.truncate()
            fh.write(encoded)
            fh.flush()
