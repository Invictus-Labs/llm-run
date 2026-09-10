"""Validate routing policy before any task is dispatched."""

from dataclasses import dataclass
from importlib.resources import files
import os
from pathlib import Path
import re
import tomllib

from llm_run.paths import config_path

_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}\Z")


class PolicyError(ValueError):
    """Invalid configuration; diagnostics deliberately omit configuration values."""


def valid_name(value: object) -> bool:
    return isinstance(value, str) and _NAME.fullmatch(value) is not None


def positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0 or value > 2_147_483_647:
        raise PolicyError(f"{label} must be a positive integer below 2147483648")
    return value


def _keys(body: dict, allowed: set[str], label: str) -> None:
    if set(body) - allowed:
        raise PolicyError(f"unknown setting in {label}")


@dataclass(frozen=True)
class EngineHop:
    engine: str
    model: str | None = None


def parse_hop(value: object) -> EngineHop:
    if not isinstance(value, str):
        raise PolicyError("chain entries must be engine or engine:model strings")
    engine, sep, model = value.partition(":")
    if not valid_name(engine) or (sep and (not model.strip() or "\x00" in model)):
        raise PolicyError("invalid engine or model identifier")
    return EngineHop(engine, model if sep else None)


@dataclass(frozen=True)
class EngineConfig:
    adapter: str | None = None
    binary: str | None = None
    command: tuple[str, ...] = ()
    timeout: int = 1800


@dataclass(frozen=True)
class Policy:
    lanes: dict[str, list[EngineHop]]
    engines: dict[str, EngineConfig]
    default_cooldown_s: int = 7200

    def chain(self, lane: str) -> list[EngineHop]:
        if lane not in self.lanes:
            raise PolicyError("unknown lane; define it in your configuration")
        return list(self.lanes[lane])


def default_text() -> str:
    return files("llm_run").joinpath("default.toml").read_text(encoding="utf-8")


def load_policy(path: Path | None = None) -> Policy:
    target = path or config_path()
    explicit = path is not None or bool(os.environ.get("LLM_RUN_CONFIG"))
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        if explicit:
            raise PolicyError("explicit configuration file does not exist") from None
        text = default_text()
    except (OSError, UnicodeError):
        raise PolicyError("cannot read configuration file") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise PolicyError("malformed configuration TOML") from None
    _keys(data, {"lanes", "engines", "routing"}, "configuration")
    lanes_raw, engines_raw = data.get("lanes"), data.get("engines")
    if not isinstance(lanes_raw, dict) or not lanes_raw:
        raise PolicyError("define at least one [lanes.NAME] table")
    if not isinstance(engines_raw, dict) or not engines_raw:
        raise PolicyError("define at least one [engines.NAME] table")
    engines = {}
    for name, body in engines_raw.items():
        if not valid_name(name) or not isinstance(body, dict):
            raise PolicyError("invalid engine definition")
        _keys(body, {"adapter", "binary", "command", "timeout"}, "engine")
        timeout = positive_int(body.get("timeout", 1800), "engine timeout")
        if "command" in body:
            command = body["command"]
            if "adapter" in body or "binary" in body:
                raise PolicyError(
                    "custom command cannot also specify adapter or binary"
                )
            if (
                not isinstance(command, list)
                or not command
                or any(
                    not isinstance(part, str) or not part or "\x00" in part
                    for part in command
                )
            ):
                raise PolicyError(
                    "command must be a nonempty array of nonempty strings"
                )
            engines[name] = EngineConfig(command=tuple(command), timeout=timeout)
        else:
            adapter = body.get("adapter")
            binary = body.get("binary", adapter)
            if adapter not in ("codex", "claude"):
                raise PolicyError("builtin adapter must be codex or claude")
            if not isinstance(binary, str) or not binary or "\x00" in binary:
                raise PolicyError("binary must be a nonempty string")
            engines[name] = EngineConfig(adapter, binary, timeout=timeout)
    lanes = {}
    for name, body in lanes_raw.items():
        if not valid_name(name) or not isinstance(body, dict):
            raise PolicyError("invalid lane definition")
        _keys(body, {"chain"}, "lane")
        chain = body.get("chain")
        if not isinstance(chain, list) or not chain:
            raise PolicyError("lane needs a nonempty chain")
        hops = [parse_hop(item) for item in chain]
        if any(hop.engine not in engines for hop in hops):
            raise PolicyError("lane references an undefined engine")
        lanes[name] = hops
    routing = data.get("routing", {})
    if not isinstance(routing, dict):
        raise PolicyError("routing must be a table")
    _keys(routing, {"default_cooldown_s"}, "routing")
    cooldown = positive_int(routing.get("default_cooldown_s", 7200), "cooldown")
    return Policy(lanes, engines, cooldown)
