"""Explicit user configuration and private local state."""

import os
from pathlib import Path


def config_path() -> Path:
    raw = os.environ.get("LLM_RUN_CONFIG")
    if raw:
        return Path(raw).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "llm-run" / "config.toml"


def state_dir() -> Path:
    raw = os.environ.get("LLM_RUN_STATE_DIR")
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = Path(raw).expanduser() if raw else base / "llm-run"
    if path.is_symlink():
        raise ValueError("state directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise ValueError("state directory must belong to the current user")
    if path.stat().st_mode & 0o077:
        raise ValueError("state directory must have private permissions (chmod 700)")
    return path.resolve()


def ledger_path() -> Path:
    return state_dir() / "ledger.sqlite"


def quota_path() -> Path:
    return state_dir() / "quota.json"
