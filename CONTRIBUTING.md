# Contributing

Use Python 3.12+ on macOS or Linux. Install development dependencies in an
isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
bash scripts/verify-quality.sh
```

Tests use synthetic local adapters; no paid accounts or provider calls are
required. The suite enforces at least 90% runtime-code coverage. Add a regression
test for changed behavior, particularly any new fallback signature. Include a
negative control showing that ordinary task output cannot trigger that signature.

Keep runtime dependencies empty. Validate configuration before dispatching a
task. Use argument arrays, preserve process cleanup, and keep prompt text and raw
provider errors out of the execution ledger and cooldown cache.

Open a pull request with the problem, changed behavior, and commands/results
used to verify it. Do not include real account data, private repository content,
runtime logs, or unsanitized provider transcripts in fixtures or issues.

Build release artifacts locally with `python -m pip wheel --no-deps .` and verify
the installed wheel from a directory outside the checkout. Review both the
wheel and source archive file lists before release.
