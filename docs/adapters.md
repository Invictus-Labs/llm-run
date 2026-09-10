# Custom adapters

A custom adapter is an executable command in configuration. It receives a
temporary prompt file, a model string (empty when unspecified), and the task
directory. The runner sets the process working directory too.

```toml
[lanes.default]
chain = ["local"]

[engines.local]
command = ["python3", "/absolute/path/to/adapter.py"]
timeout = 120
```

For every attempt the runner appends:

```text
--model MODEL --cwd ABSOLUTE_DIRECTORY --prompt-file ABSOLUTE_FILE
```

`LLM_RUN_ENGINE` contains the configured engine name. The prompt file has
permissions `0600` and is removed after completion, timeout, or interruption.
The adapter gets closed stdin and should read the file as UTF-8.

The command is launched directly as an argument array. Shell substitutions,
tilde expansion inside command arguments, and environment interpolation are
not performed. Relative paths are interpreted from the task's `--cwd`, not
from the configuration file. Use absolute paths for adapter scripts.

Custom commands are trusted programs with your user's permissions and inherited
environment. Configure only adapters you trust. Do not put secrets in command
arguments; use the adapter's own supported authentication mechanism.

## Success

Exit `0`. Stdout and stderr are captured and returned in `output` (stdout first,
then stderr; stream interleaving is not preserved). A compatible usage record
lets the runner report token counts:

```json
{"type":"result","result":"Task complete","usage":{"input_tokens":4,"output_tokens":2}}
```

Any other output format is accepted. Successful output is never classified as
a provider failure, even when it includes error examples.

## Request fallback

Exit nonzero and emit a top-level JSON record:

```json
{"type":"llm_run_error","kind":"quota","retry_after_seconds":60}
```

The recognized kinds are `quota`, `auth`, and `rejected_model`.
`retry_after_seconds` is optional, positive, and at most seven days; otherwise
the configured default cooldown applies. Auth and model rejection do not set a
cooldown. Use this protocol only when retrying the same task is appropriate.

Child exits `124` and `130` are reserved for timeout and interruption. Signal
termination is also terminal. Other nonzero exits without a recognized failure
record stop the run with exit `1`.
A timed-out or interrupted adapter never causes fallback, regardless of its
output. The runner first sends SIGTERM to its process group, then SIGKILL if
needed, with a direct-child fallback if group signalling is denied. Detached
descendants may outlive cleanup; retained pipes are closed after bounded waits.
The runner is not a sandbox for programs that detach themselves from that group.

See [the demo adapter](../examples/adapter.py) for a complete implementation.

## Built-in adapters

Codex and Claude Code run through direct argument arrays. Both receive prompts
through stdin, keeping prompt text out of their command-line arguments. A
configured `binary` is respected, including an absolute executable path.

Codex uses `exec --sandbox workspace-write --skip-git-repo-check --json`.
Claude Code uses `--print --output-format json`. Neither adapter adds permission
bypass flags. Each CLI still loads its own configuration and applies its own
authentication, permissions, billing, and persistence behavior.

The built-ins remove `ANTHROPIC_API_KEY`, `CODEX_API_KEY`, and `OPENAI_API_KEY`
from the child environment. Claude's adapter also removes `CLAUDECODE` so it
can run as a child process. These defaults favor existing CLI logins; they
cannot guarantee a particular billing mode. Custom adapters inherit the
environment unchanged except for `LLM_RUN_ENGINE`.

Selected structured provider errors are recognized conservatively. Codex JSONL
`error` and `turn.failed` messages use the [upstream event shapes](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs).
Errors nested inside a complete result envelope are not fallback signals.
