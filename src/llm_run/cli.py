"""Command-line interface. No provider calls for init, status, or report."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from llm_run import __version__
from llm_run.cooldown import Cooldowns
from llm_run.dispatch import dispatch
from llm_run.paths import config_path
from llm_run.policy import (
    PolicyError,
    default_text,
    load_policy,
    parse_hop,
    positive_int,
)
from llm_run.report import build_report, render_text


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llm-run",
        description="Run coding-agent CLIs with configured fallback and local execution records.",
    )
    p.add_argument("--version", action="version", version=f"llm-run {__version__}")
    p.add_argument("--config", type=Path, help="configuration file (before subcommand)")
    p.add_argument("--lane", default="default")
    p.add_argument(
        "--engine", help="pin one engine or engine:model; never switches engine"
    )
    source = p.add_mutually_exclusive_group()
    source.add_argument("--prompt")
    source.add_argument("--prompt-file", type=Path)
    p.add_argument("--cwd", type=Path, default=None)
    p.add_argument("--caller", default="manual")
    p.add_argument("--timeout", type=int, help="seconds per attempt")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--save-output",
        action="store_true",
        help="save redacted output locally; off by default",
    )
    subs = p.add_subparsers(dest="command")
    init = subs.add_parser(
        "init", help="write starter configuration without overwriting"
    )
    init.add_argument("--path", type=Path)
    status = subs.add_parser(
        "status", help="show binaries and observed cooldowns; no authentication probe"
    )
    status.add_argument("--json", action="store_true")
    status.add_argument("--lane", default="default")
    report = subs.add_parser(
        "report", help="summarize local attempts without calling an engine"
    )
    report.add_argument("--json", action="store_true")
    report.add_argument("--since", default="24h")
    report.add_argument("--ledger", type=Path, action="append")
    return p


def _status(policy, lane: str) -> dict:
    hops = policy.chain(lane)
    cooldowns = Cooldowns()
    engines = []
    for name, cfg in policy.engines.items():
        binary = cfg.command[0] if cfg.command else cfg.binary
        engines.append(
            {
                "engine": name,
                "available": shutil.which(binary) is not None,
                "cooldown": cooldowns.active(name),
                "until": cooldowns.get(name).get("until"),
            }
        )
    available = {
        entry["engine"]
        for entry in engines
        if entry["available"] and not entry["cooldown"]
    }
    return {
        "engines": engines,
        "eligible_chain": [h.engine for h in hops if h.engine in available],
        "warning": cooldowns.warning,
    }


def main(argv: list[str] | None = None) -> int:
    p = parser()
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 64
    try:
        if args.command == "init":
            target = (args.path or args.config or config_path()).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(default_text())
            print(f"Created {target}")
            return 0
        if args.command == "report":
            try:
                payload = build_report(args.since, args.ledger)
            except (ValueError, OverflowError):
                print(
                    "report error: invalid --since (use 24h, 7d, or YYYY-MM-DD)",
                    file=sys.stderr,
                )
                return 64
            print(
                json.dumps(payload) if args.json else render_text(payload),
                end="\n" if args.json else "",
            )
            return 0
        policy = load_policy(args.config)
        if args.command == "status":
            payload = _status(policy, args.lane)
            if args.json:
                print(json.dumps(payload))
            else:
                for entry in payload["engines"]:
                    print(
                        f"{entry['engine']}: binary={'available' if entry['available'] else 'missing'} cooldown={entry['cooldown']}"
                    )
            return 0
        if args.prompt is None and args.prompt_file is None:
            p.print_help()
            return 64
        try:
            prompt = (
                args.prompt
                if args.prompt is not None
                else args.prompt_file.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError):
            raise PolicyError("cannot read prompt file") from None
        if not prompt.strip() or "\x00" in prompt:
            raise PolicyError("prompt must be nonempty text without NUL characters")
        pin = parse_hop(args.engine) if args.engine else None
        if pin and pin.engine not in policy.engines:
            raise PolicyError("pinned engine is not defined")
        if not pin:
            policy.chain(args.lane)
        if args.timeout is not None:
            positive_int(args.timeout, "timeout")
        cwd = (args.cwd or Path.cwd()).resolve()
        if not cwd.is_dir():
            raise PolicyError("working directory does not exist")
        verdict = dispatch(
            policy=policy,
            lane=args.lane,
            prompt=prompt,
            cwd=str(cwd),
            caller=args.caller,
            timeout=args.timeout,
            pin=pin,
            save_output=args.save_output,
        )
        if args.json:
            print(json.dumps(verdict.as_json()))
        else:
            print(verdict.output, end="" if verdict.output.endswith("\n") else "\n")
            print(
                f"llm-run: {verdict.engine or '-'} exit={verdict.exit_code} depth={verdict.fallback_depth}",
                file=sys.stderr,
            )
        return verdict.exit_code
    except PolicyError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 64
    except (OSError, sqlite3.Error, ValueError):
        print(
            "llm-run: local I/O failed; check file paths, ownership, permissions, and database format",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("llm-run: interrupted", file=sys.stderr)
        return 130
