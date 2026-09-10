from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3

import pytest

from llm_run.cli import main
from llm_run.cooldown import Cooldowns
from llm_run.ledger import Ledger
from llm_run.report import collect


@pytest.mark.parametrize(
    "content",
    ["{broken", "[]", '{"a":1}', '{"a":{"until":"bad"}}', '{"a":{"until":NaN}}'],
)
def test_corrupt_or_malformed_cache_does_not_crash(tmp_path, content):
    cache = tmp_path / "quota.json"
    cache.write_text(content)
    cooldowns = Cooldowns(cache)
    assert not cooldowns.active("a")
    cooldowns.record("b", 9999999999, "quota")
    assert cooldowns.active("b")
    if content in ["{broken", "[]"]:
        assert cooldowns.warning


def test_concurrent_cache_writers_preserve_other_engines(tmp_path):
    cache = tmp_path / "quota.json"

    def record(i):
        Cooldowns(cache).record(f"engine{i}", 9999999999, "quota")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record, range(30)))
    assert len(json.loads(cache.read_text())) == 30
    with pytest.raises(ValueError):
        Cooldowns(cache).record("bad", 1, "raw output must never be stored")


def test_ledger_read_report_does_not_mutate_input_schema(tmp_path):
    ledger = Ledger(tmp_path / "example.sqlite")
    ledger.record(ts="2026-01-01T00:00:00Z", caller="demo", engine="local", status="ok")
    before = ledger.path.read_bytes()
    assert len(collect("2025-01-01T00:00:00Z", [ledger.path])) == 1
    assert ledger.path.read_bytes() == before
    with closing(sqlite3.connect(ledger.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize(
    "since", ["2026-99-99", "2026-02-30", "-1h", "99999999999999999999d"]
)
def test_invalid_report_dates_return_usage_error(since):
    assert main(["report", "--since", since]) == 64


def test_corrupt_database_reports_local_error(tmp_path, capsys):
    p = tmp_path / "corrupt.sqlite"
    p.write_text("not a database")
    assert main(["report", "--ledger", str(p)]) == 1
    assert "local I/O" in capsys.readouterr().err


def test_symlink_cache_and_database_do_not_overwrite_target(tmp_path):
    target = tmp_path / "valuable.txt"
    target.write_text("preserve")
    link = tmp_path / "quota.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        Cooldowns(link).record("demo", 1, "quota")
    with pytest.raises(OSError):
        Ledger(link)
    assert target.read_text() == "preserve"


def test_corrupt_cache_warning_is_visible_on_run(setup_policy, tmp_path, capsys):
    setup_policy()
    cache = Cooldowns()
    cache.path.write_text("{broken")
    assert main(["--prompt", "test"]) == 0
    assert "cooldown cache is invalid" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    [
        '{"a":{"until":1e309}}',
        '{"a":{"until":' + "9" * 500 + "}}",
        "[" * 2000 + "0" + "]" * 2000,
    ],
)
def test_cache_extreme_numbers_and_depth_recover(tmp_path, content):
    cache = tmp_path / "cache.json"
    cache.write_text(content)
    cooldowns = Cooldowns(cache)
    assert not cooldowns.active("a")
    cooldowns.record("b", 9999999999, "quota")
    assert cooldowns.active("b")
    assert cooldowns.warning


def test_cache_older_success_preserves_new_quota(tmp_path, monkeypatch):
    from llm_run import cooldown

    cache = Cooldowns(tmp_path / "quota.json")
    monkeypatch.setattr(cooldown.time, "time", lambda: 20)
    cache.record("a", 100, "quota")
    cache.record("a", None, "ok", unless_newer_than=10)
    assert cache.active("a")
    cache.record("a", None, "ok", unless_newer_than=30)
    assert not cache.active("a")
