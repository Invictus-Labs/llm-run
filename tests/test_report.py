"""llm-run report — caller/engine rollup, no tokens or prompt text."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_run.cli import main
from llm_run.ledger import Ledger
from llm_run.report import parse_since


def _seed(
    ledger: Ledger,
    ts: str,
    caller: str,
    engine: str,
    status: str = "ok",
    depth: int = 0,
) -> None:
    ledger.record(
        ts=ts,
        caller=caller,
        lane="review",
        prompt_hash="aa",
        prompt_bytes=2,
        cwd="/tmp",
        engine=engine,
        model=None,
        fallback_depth=depth,
        status=status,
        exit_code=0 if status == "ok" else 124,
        duration_s=1.0,
        tokens_in=99,
        tokens_out=88,
    )


def test_parse_since_relative_and_date() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    assert parse_since("8h", now) == "2026-09-02T04:00:00Z"
    assert parse_since("7d", now) == "2026-08-26T12:00:00Z"
    assert parse_since("2026-09-01") == "2026-09-01T00:00:00Z"
    assert parse_since("2026-09-01T15:30:00Z") == "2026-09-01T15:30:00Z"
    naive = datetime(2026, 9, 2, 12, 0)
    assert parse_since("1h", naive) == "2026-09-02T11:00:00Z"
    with pytest.raises(ValueError):
        parse_since("nope")
    with pytest.raises(ValueError):
        parse_since("0h")
    with pytest.raises(ValueError):
        parse_since("")


def test_report_empty_ledger_and_missing_path(
    isolated: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["report", "--since", "8h"]) == 0
    out = capsys.readouterr().out
    assert "hops=0" in out
    assert "(none)" in out
    missing = tmp_path / "nope.sqlite"
    assert main(["report", "--since", "8h", "--ledger", str(missing)]) == 0
    assert "hops=0" in capsys.readouterr().out


def test_report_groups_by_caller_engine_status(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = Ledger()
    _seed(ledger, "2026-09-02T01:00:00Z", "review-job", "codex")
    _seed(ledger, "2026-09-02T01:10:00Z", "review-job", "codex")
    _seed(ledger, "2026-09-02T01:20:00Z", "review-job", "codex", "timeout")
    _seed(ledger, "2026-09-02T01:30:00Z", "build-job", "grok", depth=1)
    assert main(["report", "--since", "2026-09-01", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 4
    assert payload["by_engine"] == {"codex": 3, "grok": 1}
    by = {(g["caller"], g["engine"], g["status"]): g for g in payload["groups"]}
    assert by[("review-job", "codex", "ok")]["n"] == 2
    assert by[("review-job", "codex", "timeout")]["n"] == 1
    assert by[("build-job", "grok", "ok")]["fallback_gt_0"] == 1


def test_report_json_omits_tokens_and_prompt(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(Ledger(), "2026-09-02T01:00:00Z", "review", "codex")
    assert main(["report", "--since", "2026-09-01", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    blob = json.dumps(payload)
    assert "tokens" not in blob
    assert "prompt" not in blob
    assert payload["rows"] == 1
    assert payload["by_engine"] == {"codex": 1}
    assert payload["by_caller"] == {"review": 1}
    assert payload["groups"][0]["n"] == 1
    assert payload["groups"][0]["fallback_gt_0"] == 0


def test_report_since_filters_old_rows(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = Ledger()
    _seed(ledger, "2020-01-01T00:00:00Z", "old", "claude")
    _seed(ledger, "2026-09-02T10:00:00Z", "new", "codex")
    assert main(["report", "--since", "2026-09-01", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 1
    assert payload["by_caller"] == {"new": 1}


def test_report_text_and_invalid_since(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(Ledger(), "2026-09-02T01:00:00Z", "review", "codex")
    assert main(["report", "--since", "2026-09-01"]) == 0
    out = capsys.readouterr().out
    assert "by engine" in out
    assert "codex" in out
    assert "review" in out
    assert main(["report", "--since", "bogus"]) == 64
    assert "report error" in capsys.readouterr().err


def test_report_union_two_ledgers(
    isolated: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other.sqlite"
    _seed(Ledger(), "2026-09-02T01:00:00Z", "worker-a", "codex")
    _seed(Ledger(other), "2026-09-02T01:05:00Z", "worker-b", "grok")
    default = isolated / "state" / "ledger.sqlite"
    assert (
        main(
            [
                "report",
                "--since",
                "2026-09-01",
                "--json",
                "--ledger",
                str(default),
                "--ledger",
                str(other),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 2
    assert payload["by_engine"] == {"codex": 1, "grok": 1}
    assert set(payload["by_caller"]) == {"worker-a", "worker-b"}
