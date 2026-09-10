"""Ordered dispatch with local cooldowns and one ledger row per attempt or skip."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import sys
import tempfile
import time

from llm_run.adapter import run_adapter
from llm_run.cooldown import Cooldowns
from llm_run.ledger import Ledger, prompt_fingerprint
from llm_run.paths import state_dir
from llm_run.policy import EngineHop, Policy
from llm_run.scrub import scrub
from llm_run.signatures import classify_output, parse_reset_epoch
from llm_run.usage import parse_tokens


@dataclass
class Verdict:
    engine: str | None
    model: str | None
    lane: str
    exit_code: int
    fallback_depth: int
    duration_s: float
    output: str = ""
    output_file: str | None = None
    tokens: dict[str, int] | None = None

    def as_json(self) -> dict:
        return asdict(self)


def _write_output(output: str) -> str:
    fd, path = tempfile.mkstemp(prefix="output-", suffix=".log", dir=state_dir())
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(scrub(output))
    return path


def dispatch(
    *,
    policy: Policy,
    lane: str,
    prompt: str,
    cwd: str,
    caller: str,
    timeout: int | None = None,
    pin: EngineHop | None = None,
    save_output: bool = False,
) -> Verdict:
    hops = [pin] if pin else policy.chain(lane)
    ledger, cooldowns = Ledger(), Cooldowns()
    digest, nbytes = prompt_fingerprint(prompt)
    started = time.monotonic()
    last_output, last_file = "", None
    for depth, hop in enumerate(hops):
        cfg = policy.engines[hop.engine]
        row = dict(
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            caller=caller,
            lane=lane,
            prompt_hash=digest,
            prompt_bytes=nbytes,
            cwd=cwd,
            engine=hop.engine,
            model=hop.model,
            fallback_depth=depth,
        )
        if cooldowns.active(hop.engine):
            ledger.record(**row, status="skipped", duration_s=0, quota_event="cooldown")
            continue
        result = run_adapter(
            hop, cfg, prompt=prompt, cwd=cwd, timeout=timeout or cfg.timeout
        )
        last_output = result.output
        last_file = _write_output(last_output) if save_output else None
        # A successful task may legitimately discuss provider errors. The exit
        # gate is essential; do not move classification above it.
        kind = (
            None
            if result.exit_code == 0 or result.terminal
            else classify_output(result.output, hop.model)
        )
        tokens, _ = parse_tokens(result.output)
        if kind:
            until = (
                parse_reset_epoch(result.output, policy.default_cooldown_s)
                if kind == "quota"
                else None
            )
            cooldowns.record(hop.engine, until, kind)
            ledger.record(
                **row,
                status=kind,
                exit_code=result.exit_code,
                duration_s=result.duration_s,
                quota_event=kind,
            )
            print(
                f"llm-run: {hop.engine}: {kind}; {'pinned attempt ended' if pin else 'trying next configured engine'}",
                file=sys.stderr,
            )
            continue
        if result.exit_code == 0:
            cooldowns.record(hop.engine, None, "ok")
        code = result.exit_code if result.exit_code in (0, 124, 130) else 1
        status = {0: "ok", 124: "timeout", 130: "interrupted"}.get(code, "error")
        ledger.record(
            **row,
            status=status,
            exit_code=result.exit_code,
            duration_s=result.duration_s,
            tokens_in=(tokens or {}).get("in"),
            tokens_out=(tokens or {}).get("out"),
        )
        if cooldowns.warning:
            print(f"llm-run: {cooldowns.warning}", file=sys.stderr)
        return Verdict(
            hop.engine,
            hop.model,
            lane,
            code,
            depth,
            round(time.monotonic() - started, 3),
            last_output,
            last_file,
            tokens,
        )
    print("llm-run: configured chain exhausted", file=sys.stderr)
    return Verdict(
        None,
        None,
        lane,
        75,
        len(hops),
        round(time.monotonic() - started, 3),
        last_output,
        last_file,
    )
