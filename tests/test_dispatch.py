import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from llm_run.cli import main
from llm_run.cooldown import Cooldowns
from llm_run.ledger import Ledger, prompt_fingerprint
from llm_run.signatures import classify_output


def calls(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_success_subprocess_cwd_json_and_prompt_cleanup(setup_policy, tmp_path, capsys):
    _, capture = setup_policy()
    prompt = 'literal $(touch not-created); "quotes"\nsecond line'
    assert main(["--prompt", prompt, "--cwd", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert json.loads(payload["output"])["result"] == prompt
    assert payload["tokens"] == {"in": 4, "out": 2}
    assert payload["output_file"] is None
    attempt = calls(capture)[0]
    assert attempt["prompt"] == prompt
    assert attempt["cwd"] == str(tmp_path)
    assert attempt["mode"] == 0o600
    assert not Path(attempt["prompt_file"]).exists()
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("kind", ["quota", "auth", "rejected_model", "provider-quota"])
def test_fallback_and_metadata_privacy(setup_policy, tmp_path, capsys, kind):
    _, capture = setup_policy(kind)
    prompt = "private-example-sentinel-unique"
    assert main(["--prompt", prompt, "--json"]) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["engine"] == "second" and verdict["fallback_depth"] == 1
    assert [call["engine"] for call in calls(capture)] == ["first", "second"]
    expected = "quota" if kind == "provider-quota" else kind
    rows = Ledger().rows()
    assert [row["status"] for row in rows] == [expected, "ok"]
    assert rows[0]["quota_event"] == expected
    assert rows[0]["prompt_hash"] == prompt_fingerprint(prompt)[0]
    assert rows[0]["prompt_bytes"] == len(prompt.encode())
    for file in (tmp_path / "state").iterdir():
        assert prompt.encode() not in file.read_bytes()
    assert not list((tmp_path / "state").glob("*.log"))
    assert (
        not Cooldowns().active("first")
        if kind in ("auth", "rejected_model")
        else Cooldowns().active("first")
    )


def test_second_run_skips_cooldown_and_records_skip(setup_policy):
    _, capture = setup_policy("quota")
    assert main(["--prompt", "first"]) == 0
    assert main(["--prompt", "second"]) == 0
    assert [call["engine"] for call in calls(capture)] == ["first", "second", "second"]
    assert [row["status"] for row in Ledger().rows()] == [
        "quota",
        "ok",
        "skipped",
        "ok",
    ]


@pytest.mark.parametrize(
    "first,code", [("error", 1), ("rejection-success", 0), ("binary", 0)]
)
def test_terminal_results_never_fallback(setup_policy, capsys, first, code):
    _, capture = setup_policy(first)
    assert main(["--prompt", "example", "--json"]) == code
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["engine"] == "first"
    assert len(calls(capture)) == 1
    if first == "rejection-success":
        # Arm the control: removing the success exit-code gate must fail it.
        assert classify_output(verdict["output"]) == "quota"
        assert not Cooldowns().active("first")


def test_exhausted_and_pinned_runs(setup_policy, capsys):
    _, capture = setup_policy("quota", "auth")
    assert (
        main(
            [
                "--prompt",
                "test",
                "--engine",
                "first:example-model",
                "--lane",
                "undefined",
                "--json",
            ]
        )
        == 75
    )
    assert len(calls(capture)) == 1
    assert (
        calls(capture)[0]["argv"][calls(capture)[0]["argv"].index("--model") + 1]
        == "example-model"
    )
    assert json.loads(capsys.readouterr().out)["engine"] is None
    assert main(["--prompt", "again", "--json"]) == 75
    assert [r["status"] for r in Ledger().rows()] == ["quota", "skipped", "auth"]


@pytest.mark.parametrize("behavior", ["timeout", "ignore-term"])
def test_timeout_kills_process_and_cleans_prompt_without_fallback(
    setup_policy, behavior
):
    _, capture = setup_policy(behavior)
    assert main(["--prompt", "timeout", "--timeout", "1"]) == 124
    attempt = calls(capture)[0]
    assert len(calls(capture)) == 1
    assert not Path(attempt["prompt_file"]).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(attempt["pid"], 0)
    assert Ledger().rows()[0]["status"] == "timeout"


def test_interrupt_cleans_child_and_prompt(setup_policy, tmp_path):
    _, capture = setup_policy("timeout")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "llm_run", "--prompt", "interrupt", "--json"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not capture.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert capture.exists()
        attempt = calls(capture)[0]
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=8)
        assert proc.returncode == 130
        assert json.loads(out)["exit_code"] == 130
        assert not Path(attempt["prompt_file"]).exists()
        with pytest.raises(ProcessLookupError):
            os.kill(attempt["pid"], 0)
        assert len(calls(capture)) == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize("builtin", ["codex", "claude"])
def test_builtin_binary_override_stdin_and_credentials(
    setup_policy, monkeypatch, tmp_path, builtin
):
    _, capture = setup_policy(builtin=builtin)
    for key in ("ANTHROPIC_API_KEY", "CODEX_API_KEY", "OPENAI_API_KEY", "CLAUDECODE"):
        monkeypatch.setenv(key, "synthetic-marker")
    assert (
        main(
            [
                "--engine",
                "first:sample-model",
                "--prompt",
                "-literal prompt",
                "--cwd",
                str(tmp_path),
            ]
        )
        == 0
    )
    attempt = calls(capture)[0]
    assert attempt["prompt"] == "-literal prompt"
    assert "-literal prompt" not in attempt["argv"]
    assert not set(attempt["keys_present"]) & {
        "ANTHROPIC_API_KEY",
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
    }
    assert attempt["cwd"] == str(tmp_path)
    assert "sample-model" in attempt["argv"]
    if builtin == "codex":
        assert "workspace-write" in attempt["argv"]
        assert attempt["argv"][-1] == "-"
    else:
        assert "CLAUDECODE" not in attempt["keys_present"]


def test_output_opt_in_permissions_and_redaction(setup_policy, tmp_path, capsys):
    setup_policy()
    # Synthetic credential-shaped material tests the scrubber, not real credentials.
    secret = "sk-" + "SYNTHETIC" * 4
    assert main(["--prompt", secret, "--save-output", "--json"]) == 0
    path = Path(json.loads(capsys.readouterr().out)["output_file"])
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    assert secret not in path.read_text()
    assert "<redacted>" in path.read_text()
    assert (tmp_path / "state" / "ledger.sqlite").stat().st_mode & 0o777 == 0o600


def test_missing_binary_is_terminal(setup_policy, capsys):
    config, _ = setup_policy()
    text = config.read_text().replace(sys.executable, "no-such-executable-for-test", 1)
    config.write_text(text)
    assert main(["--prompt", "test", "--json"]) == 1
    verdict = json.loads(capsys.readouterr().out)
    assert "could not start adapter" in verdict["output"]
    assert [r["engine"] for r in Ledger().rows()] == ["first"]


def test_prompt_file_input(setup_policy, tmp_path):
    _, capture = setup_policy()
    path = tmp_path / "prompt.txt"
    path.write_text("multiline\nprompt")
    assert main(["--prompt-file", str(path)]) == 0
    assert calls(capture)[0]["prompt"] == "multiline\nprompt"
