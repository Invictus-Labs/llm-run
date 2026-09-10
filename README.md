# llm-run

**One command. Your coding agents. A record of every attempt.**

`llm-run` runs a task through a configurable chain of coding-agent CLIs. It can
try the next configured engine when a provider reports a quota, authentication,
or model-availability error, and remembers observed cooldowns locally.

Built for developers who use more than one coding agent and want explicit
routing, predictable failure behavior, and a local execution history.

- Built-in adapters for **Codex** and **Claude Code**; bring other tools through a custom adapter.
- Engine and model selection live in **your configuration**.
- **Timeouts and ordinary task failures stop the chain.**
- **Zero runtime dependencies** beyond Python 3.12+ and your chosen CLI.
- **No telemetry or credential discovery.** The core makes no network requests.
- Output is returned to your terminal; saving it is **opt-in**.

Early release for macOS and Linux. Provider integrations are tested with fake
executables against locally inspected CLI help; live account behavior is not
covered by the offline test suite.

## Install

```bash
git clone https://github.com/Invictus-Labs/llm-run.git
cd llm-run
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
llm-run --version
```

The distribution is named `invictus-llm-run`; the command is `llm-run`.
Install from this repository or the release wheel. It is not published to PyPI.

## Try it without an account

From the repository root, after installation:

```bash
python examples/demo.py
```

The demo uses synthetic local adapters. The first returns a quota error, the
second returns a result. A second run skips the cooled-down adapter. Finally,
the demo prints the execution report. No provider, account, or network is used;
its temporary configuration and state are deleted on exit.

## Run a task

Install and log in to the agent CLIs you choose, then create your configuration:

```bash
llm-run init
llm-run status
llm-run --prompt "Summarize the architecture of this repository."
llm-run --engine codex --prompt-file task.txt --timeout 300 --json
llm-run report --since 24h
```

The starter policy tries **Codex, then Claude Code**, using each CLI's default
model. Edit the chain to select which providers may receive your task. Pinning
with `--engine` restricts the run to that engine, including when it fails.

Fallback starts a **new attempt with the same prompt and working directory**.
It does not transfer conversation history, undo earlier edits, or guarantee
exactly-once execution. Choose tasks and provider chains accordingly.

## Configuration

```toml
[lanes.default]
chain = ["codex", "claude"]

[engines.codex]
adapter = "codex"
binary = "codex"
timeout = 1800

[engines.claude]
adapter = "claude"
binary = "claude"
timeout = 1800

[routing]
default_cooldown_s = 7200
```

Choose a model with `engine:model-id` in a chain or `--engine engine:model-id`.
Model availability belongs to the provider; this package ships no model pins.
`binary` can be an absolute path or a command on `PATH`.

Configuration precedence:

1. `--config PATH` (place it before a subcommand).
2. `LLM_RUN_CONFIG`.
3. `$XDG_CONFIG_HOME/llm-run/config.toml`, or `~/.config/llm-run/config.toml`.
4. Bundled starter configuration when no user config exists.

An explicitly selected missing or invalid configuration is an error. A config
file in the working directory is never loaded automatically. `init` refuses
to overwrite an existing file.

```bash
llm-run --config ./config.local.toml status --json
llm-run init --path ./config.local.toml
```

Use `--cwd` to choose the task directory. Each engine's timeout applies per
attempt; `--timeout` overrides it for this run. The total duration can span
several attempts.

## Results and exit codes

Normal mode prints the child output to stdout and a routing summary to stderr.
`--json` prints one result object containing `engine`, `model`, `lane`,
`exit_code`, `fallback_depth`, `duration_s`, `output`, `output_file`, and
`tokens`. Output remains in each provider CLI's native format. Token counts
are best-effort and may be `null`; they are not a billing estimate.

| Exit | Meaning |
|---|---|
| `0` | Child completed successfully |
| `1` | Task, adapter, or local I/O failure; no automatic fallback |
| `64` | Invalid arguments or configuration |
| `75` | No configured attempt succeeded; includes exhausted pinned runs |
| `124` | Timeout; process group terminated, no fallback |
| `130` | Interrupted; process group terminated, no fallback |

`status` checks binary availability and observed cooldowns. It does not verify
login, measure remaining subscription quota, or contact a provider.

## Local data

State lives in `$XDG_STATE_HOME/llm-run` (normally `~/.local/state/llm-run`),
or the explicit `LLM_RUN_STATE_DIR`. The directory must belong to you and have
permissions `0700`; newly created state directories use that mode.

The SQLite ledger stores a prompt hash and byte count, timestamps, caller,
working directory, engine/model, outcome, duration, and available token counts.
It does not store prompt text or raw error messages. The cooldown cache stores
classification labels and times. These records remain local and can still
describe private work; do not commit your state directory.

`--save-output` writes a `0600` log with best-effort credential redaction.
Redaction cannot remove every secret or private detail. Output may include
prompt text, repository content, and personal data. See [Privacy](docs/privacy.md).

## Extend and contribute

- [Custom adapter contract](docs/adapters.md)
- [Architecture and failure behavior](docs/architecture.md)
- [Contributing and local checks](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)

Extracted from tools used at Invictus Labs. Released as an independent package
with generic configuration and synthetic examples.

MIT licensed. Created by Jeremy Knox.
