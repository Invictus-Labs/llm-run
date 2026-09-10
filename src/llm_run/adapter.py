"""Subprocess adapters: argument arrays, explicit cwd, bounded termination."""

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from llm_run.policy import EngineConfig, EngineHop


@dataclass
class AdapterResult:
    exit_code: int
    output: str
    duration_s: float
    terminal: bool = False


def _stop(proc: subprocess.Popen) -> tuple[bytes, bytes]:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return proc.communicate(timeout=2)


def run_adapter(
    hop: EngineHop, cfg: EngineConfig, *, prompt: str, cwd: str, timeout: int
) -> AdapterResult:
    env = os.environ.copy()
    env["LLM_RUN_ENGINE"] = hop.engine
    prompt_file: str | None = None
    if cfg.command:
        fd, prompt_file = tempfile.mkstemp(suffix=".prompt")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(prompt)
        command = [
            *cfg.command,
            "--model",
            hop.model or "",
            "--cwd",
            cwd,
            "--prompt-file",
            prompt_file,
        ]
        input_bytes = None
    else:
        for name in ("ANTHROPIC_API_KEY", "CODEX_API_KEY", "OPENAI_API_KEY"):
            env.pop(name, None)
        model_args = ["--model", hop.model] if hop.model else []
        if cfg.adapter == "codex":
            command = [
                cfg.binary,
                "exec",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--json",
                *model_args,
                "--cd",
                cwd,
                "-",
            ]
        else:
            env.pop("CLAUDECODE", None)
            command = [cfg.binary, "--print", "--output-format", "json", *model_args]
        input_bytes = prompt.encode("utf-8")
    started = time.monotonic()
    proc = None
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        code, terminal = None, False
        try:
            stdout, stderr = proc.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            code, terminal = 124, True
            stdout, stderr = _stop(proc)
        except KeyboardInterrupt:
            code, terminal = 130, True
            stdout, stderr = _stop(proc)
        output = stdout.decode("utf-8", "replace") + stderr.decode("utf-8", "replace")
        return AdapterResult(
            code if code is not None else proc.returncode,
            output,
            time.monotonic() - started,
            terminal,
        )
    except OSError as exc:
        return AdapterResult(
            1,
            f"could not start adapter ({exc.strerror})\n",
            time.monotonic() - started,
            True,
        )
    finally:
        if proc is not None and proc.poll() is None:
            _stop(proc)
        if prompt_file:
            Path(prompt_file).unlink(missing_ok=True)
