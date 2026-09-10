import json
from pathlib import Path

import pytest

from llm_run.cli import main
from llm_run.paths import config_path, state_dir
from llm_run.policy import PolicyError, default_text, load_policy, parse_hop


@pytest.mark.parametrize(
    "suffix",
    [
        "\nwrong=1",
        "\n[engines.bad]\ncommand=[]",
        '\n[engines.bad]\ncommand=[""]',
        '\n[engines.bad]\ncommand="echo"',
        '\n[engines.bad]\ncommand=["echo"]\nadapter="codex"',
        '\n[engines.bad]\nadapter="unknown"',
        '\n[engines.bad]\nadapter="claude"\nbinary=42',
        '\n[engines.bad]\nadapter="codex"\ntimeout=0',
        '\n[engines.bad]\nadapter="codex"\ntimeout=true',
        '\n[engines.bad]\nadapter="codex"\ntimeout=1.5',
        '\n[engines.bad]\nadapter="codex"\nextra_flags=["unsafe"]',
        "\n[lanes.empty]\nchain=[]",
        '\n[lanes.undefined]\nchain=["not-defined"]',
        "\n[lanes.invalid]\nchain=[12]",
        '\n[lanes.invalid]\nchain=["../bad"]',
        '\n[lanes.invalid]\nchain=["codex:"]',
        '\n[lanes.invalid]\nchain=["codex"]\nextra=1',
    ],
)
def test_invalid_policies_fail_before_dispatch(tmp_path, suffix):
    p = tmp_path / "bad.toml"
    p.write_text(default_text() + suffix)
    with pytest.raises(PolicyError):
        load_policy(p)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "lanes=1",
        '[lanes.default]\nchain=["a"]',
        "engines=[]\nlanes=[]",
        "lanes={a=1}\nengines={a={adapter='codex'}}",
        "lanes={a={chain=['a']}}\nengines={a=1}",
        "lanes={a={chain=['a']}}\nengines={a={adapter='codex'}}\nrouting=2",
        "lanes={a={chain=['a']}}\nengines={a={adapter='codex'}}\nrouting={default_cooldown_s=-1}",
        "lanes={a={chain=['a']}}\nengines={a={adapter='codex'}}\nunknown=1",
        "this is not TOML",
    ],
)
def test_invalid_shapes(tmp_path, text):
    p = tmp_path / "bad.toml"
    p.write_text(text)
    with pytest.raises(PolicyError):
        load_policy(p)


def test_explicit_missing_file_never_uses_defaults(tmp_path, monkeypatch, capsys):
    p = tmp_path / "missing.toml"
    monkeypatch.setenv("LLM_RUN_CONFIG", str(p))
    assert main(["--prompt", "test"]) == 64
    assert "explicit configuration" in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


def test_no_cwd_config_discovery_and_init_refuses_overwrite(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("malformed secret-looking content")
    assert list(load_policy().engines) == ["codex", "claude"]
    p = tmp_path / "nested" / "config.toml"
    assert main(["init", "--path", str(p)]) == 0
    original = p.read_bytes()
    assert p.stat().st_mode & 0o777 == 0o600
    assert main(["init", "--path", str(p)]) == 1
    assert p.read_bytes() == original
    assert "local I/O" in capsys.readouterr().err


def test_config_precedence(tmp_path, monkeypatch):
    selected = tmp_path / "chosen.toml"
    selected.write_text(
        default_text().replace('chain = ["codex", "claude"]', 'chain = ["claude"]')
    )
    monkeypatch.setenv("LLM_RUN_CONFIG", str(tmp_path / "missing"))
    assert [hop.engine for hop in load_policy(selected).chain("default")] == ["claude"]
    assert parse_hop("claude:example:model").model == "example:model"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--prompt", ""],
        ["--prompt", "   "],
        ["--prompt", "\x00"],
        ["--prompt", "x", "--timeout", "0"],
        ["--prompt", "x", "--timeout", "-1"],
        ["--prompt", "x", "--engine", "unknown"],
        ["--prompt", "x", "--engine", "../x"],
        ["--prompt", "x", "--lane", "unknown"],
        ["--prompt-file", "/missing-test-prompt"],
        ["--prompt", "x", "--prompt-file", "also"],
        ["--unknown-argument"],
        ["--prompt", "x", "--cwd", "/missing-test-cwd"],
        [],
    ],
)
def test_bad_cli_arguments_do_not_spawn(arguments, tmp_path):
    assert main(arguments) == 64
    assert not (tmp_path / "state").exists()


def test_help_version_default_init_and_config_locations(tmp_path, monkeypatch, capsys):
    assert main(["--help"]) == 0
    assert main(["--version"]) == 0
    assert "0.1.0" in capsys.readouterr().out
    assert main(["init"]) == 0
    assert config_path().is_file()
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert config_path() == tmp_path / "home/.config/llm-run/config.toml"
    monkeypatch.delenv("LLM_RUN_STATE_DIR")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    assert state_dir() == tmp_path / "xdg-state/llm-run"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert state_dir() == tmp_path / "home/.local/state/llm-run"


def test_status_does_not_launch_or_claim_authentication(setup_policy, capsys):
    _, capture = setup_policy()
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["eligible_chain"] == ["first", "second"]
    assert "auth" not in json.dumps(payload)
    assert not capture.exists()
    assert main(["status"]) == 0
    assert "binary=available" in capsys.readouterr().out


def test_private_state_rejects_symlink_or_public_directory(tmp_path, monkeypatch):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    monkeypatch.setenv("LLM_RUN_STATE_DIR", str(public))
    with pytest.raises(ValueError, match="private permissions"):
        state_dir()
    target = tmp_path / "link"
    target.symlink_to(public)
    monkeypatch.setenv("LLM_RUN_STATE_DIR", str(target))
    with pytest.raises(ValueError, match="symlink"):
        state_dir()


@pytest.mark.parametrize(
    "args",
    [
        ["--json", "status"],
        ["status", "--json"],
        ["--json", "report"],
        ["report", "--json"],
    ],
)
def test_json_option_works_before_and_after_subcommand(args, capsys):
    assert main(args) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


@pytest.mark.parametrize(
    "args",
    [
        ["--lane", "alternate", "status", "--json"],
        ["status", "--lane", "alternate", "--json"],
    ],
)
def test_status_lane_option_works_before_and_after_subcommand(
    setup_policy, args, capsys
):
    config, _ = setup_policy()
    config.write_text(config.read_text() + '\n[lanes.alternate]\nchain=["second"]\n')
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["eligible_chain"] == ["second"]
