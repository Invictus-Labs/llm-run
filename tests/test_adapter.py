import io
import subprocess
from unittest.mock import Mock

from llm_run import adapter
from llm_run.policy import EngineConfig, EngineHop


def test_detached_pipe_timeout_retains_terminal_result_and_cleans_prompt(
    monkeypatch, tmp_path
):
    original = adapter.tempfile.mkstemp

    def create(**kwargs):
        return original(dir=tmp_path, **kwargs)

    monkeypatch.setattr(adapter.tempfile, "mkstemp", create)
    proc = Mock(
        pid=123456789,
        returncode=-9,
        stdin=None,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )
    record = b'{"type":"llm_run_error","kind":"quota"}'
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired("fake", 1, record, b"progress")
    ] * 3
    proc.poll.return_value = -9
    proc.wait.return_value = -9
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(adapter.os, "killpg", lambda *a: None)
    result = adapter.run_adapter(
        EngineHop("fake"),
        EngineConfig(command=("fake",)),
        prompt="test",
        cwd=str(tmp_path),
        timeout=1,
    )
    assert result.exit_code == 124 and result.terminal
    assert result.output == record.decode() + "\nprogress"
    assert proc.communicate.call_count == 3
    assert proc.stdout.closed and proc.stderr.closed
    assert not list(tmp_path.glob("*.prompt"))


def test_restricted_group_signalling_stops_direct_child(monkeypatch):
    def denied(*args):
        raise PermissionError

    monkeypatch.setattr(adapter.os, "killpg", denied)
    proc = Mock()
    proc.communicate.return_value = (b"", b"")
    assert adapter._stop(proc) == (b"", b"")
    assert proc.send_signal.call_count == 2
