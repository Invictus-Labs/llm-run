"""Dispatch-ledger rollup — who used which engine. No tokens, no prompt text."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from llm_run.ledger import Ledger
from llm_run.paths import ledger_path

_GROUP_KEYS = ("caller", "lane", "engine", "status")


def parse_since(raw: str, now: datetime | None = None) -> str:
    """Return UTC ISO cutoff. Accepts 8h / 24h / 7d or YYYY-MM-DD[THH:MM:SSZ]."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    text = (raw or "").strip()
    if not text:
        raise ValueError("since is empty")
    if len(text) >= 2 and text[-1] in "hd" and text[:-1].isdigit():
        n = int(text[:-1])
        if n <= 0:
            raise ValueError(f"invalid since: {raw!r}")
        delta = timedelta(hours=n) if text[-1] == "h" else timedelta(days=n)
        cutoff = now - delta
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        datetime.strptime(text, "%Y-%m-%d")
        return f"{text}T00:00:00Z"
    if text.endswith("Z") and "T" in text:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
        return text
    raise ValueError(f"invalid since: {raw!r} (use 8h, 24h, 7d, or YYYY-MM-DD)")


def _clean(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "ts": row.get("ts"),
        "caller": row.get("caller") or "unknown",
        "lane": row.get("lane") or "-",
        "engine": row.get("engine") or "-",
        "status": row.get("status") or "-",
        "fallback_depth": int(row.get("fallback_depth") or 0),
        "exit_code": row.get("exit_code"),
        "source": source,
    }


def collect(since_iso: str, ledgers: list[Path]) -> list[dict[str, Any]]:
    hops: list[dict[str, Any]] = []
    for path in ledgers:
        if not path.is_file():
            continue
        for row in Ledger.read_since(path, since_iso):
            hops.append(_clean(row, str(path)))
    hops.sort(key=lambda r: (r["ts"] or "", r["source"]))
    return hops


def summarize(hops: list[dict[str, Any]], since_iso: str) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for hop in hops:
        key = tuple(hop[k] for k in _GROUP_KEYS)
        slot = groups.get(key)
        if slot is None:
            slot = {
                "caller": hop["caller"],
                "lane": hop["lane"],
                "engine": hop["engine"],
                "status": hop["status"],
                "n": 0,
                "fallback_gt_0": 0,
            }
            groups[key] = slot
        slot["n"] += 1
        if hop["fallback_depth"] > 0:
            slot["fallback_gt_0"] += 1
    grouped = sorted(groups.values(), key=lambda g: (-g["n"], g["caller"], g["engine"]))
    return {
        "since": since_iso,
        "rows": len(hops),
        "by_engine": dict(Counter(h["engine"] for h in hops)),
        "by_caller": dict(Counter(h["caller"] for h in hops)),
        "groups": grouped,
    }


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        f"llm-run report  since {payload['since']}  hops={payload['rows']}",
        "",
        "by engine",
    ]
    engines = payload["by_engine"]
    if not engines:
        lines.append("  (none)")
    else:
        for name, n in sorted(engines.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {name:16} {n}")
    lines += ["", "by caller"]
    callers = payload["by_caller"]
    if not callers:
        lines.append("  (none)")
    else:
        for name, n in sorted(callers.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {name:24} {n}")
    lines += ["", "hops"]
    if not payload["groups"]:
        lines.append("  (none)")
        return "\n".join(lines) + "\n"
    lines.append(
        f"  {'caller':24} {'lane':12} {'engine':10} {'status':12} {'n':>4} {'depth>0':>7}"
    )
    for g in payload["groups"]:
        lines.append(
            f"  {g['caller'][:24]:24} {g['lane'][:12]:12} {g['engine'][:10]:10} "
            f"{g['status'][:12]:12} {g['n']:4} {g['fallback_gt_0']:7}"
        )
    return "\n".join(lines) + "\n"


def build_report(since: str, ledger_paths: list[Path] | None = None) -> dict[str, Any]:
    since_iso = parse_since(since)
    paths = ledger_paths or [ledger_path()]
    return summarize(collect(since_iso, paths), since_iso)
