# Privacy and configuration boundaries

The core contains no HTTP client, telemetry endpoint, account-file discovery,
or automatic remote notifications. It reads the selected routing configuration
and its own state directory. Running a built-in provider CLI sends the task to
that CLI and any services it uses; choose the providers in your chain explicitly.

## What is persisted

| File | Contents |
|---|---|
| `ledger.sqlite` | Attempt metadata, prompt SHA256 and UTF-8 byte count, caller, task directory, engine/model, time, result, duration, optional token counts |
| `quota.json` | Engine name, observed cooldown expiry, check time, classification label |
| `output-*.log` | Child output, only when `--save-output` is specified |

Prompt hashes are fingerprints, not encryption or anonymization. Someone with
the ledger can recognize a guessed prompt by hashing it. Caller labels and
working-directory paths may reveal private project information. The report
aggregates caller/engine/outcome counts; it does not include prompt hashes,
working directories, or hostnames.

New state directories use `0700`, and new database/cache/output files use
`0600`. An existing state directory with broader access is rejected. Keep that
directory outside your source repository. If migrating existing files yourself,
ensure their permissions are private too.

Output is held in memory and returned to the caller. With a custom adapter,
the prompt also briefly exists in a `0600` temporary file, removed on normal
completion, handled errors, timeout, and Ctrl-C. An uncatchable process kill or
machine crash can prevent cleanup. No application can guarantee deletion after
those events. The built-ins send the prompt through stdin.

`--save-output` redacts several common credential shapes. It does not recognize
every credential, private source file, personal detail, or echoed prompt. Treat
saved output and redirected terminal output as sensitive.

## What belongs outside the public project

Keep account configuration, model preferences, absolute operational paths,
provider credentials, and execution state in user-managed files. Public examples
use synthetic tasks and adapters. No settings are discovered in a task's current
directory automatically.

Your chosen CLI may independently load project instructions, configuration,
plugins, credentials, hooks, or MCP servers, and may store transcripts. Those
behaviors are outside this runner's privacy guarantees. Review the CLI's
configuration before running it on private material.
