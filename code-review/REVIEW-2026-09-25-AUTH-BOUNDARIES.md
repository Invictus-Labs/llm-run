# Code review — authentication and cooldown boundaries

## Scope
Reviewed cac9ace..d1d59ee: seven files covering provider error handling, regression tests, architecture documentation and review-note cleanup.

## Verdict
APPROVED. No open findings across security, logic, tests, consistency and performance.

## Behavior verified
- Authentication-like task prose stops without creating authentication state or retrying another engine.
- Explicit provider authentication errors retain fallback behavior.
- Cooldown intervals come from recognized provider quota messages or explicit adapter retry intervals, ignoring task prose before or after them.
- Examples and documentation remain generic and portable. No account-file discovery, telemetry endpoint or runtime dependency was introduced.

## Validation
Full quality gate:195tests passed,96.88%coverage. Regression tests cover plain and structured quota messages, task/result noise, absent hints and custom intervals. Authentication-state and reset-parser mutations were detected by assertions. Independent reset checks:14passed; restoring the previous parser triggered13assertion failures. Controlled searches supported all five review dimensions; full source and caller inspection also performed. No live providers used.

## Coverage map
| Contract | Evidence |
|---|---|
| Provider authentication fallback | Two-adapter order and authentication/success ledger assertions |
| Ordinary task error | Single invocation, terminal error and empty authentication state |
| Provider-only reset intervals | Plain, error, turn.failed and nested error formats with task noise in both orders |
| Missing reset hint | Default cooldown despite a duration in task output |
| Explicit adapter interval | Bounded retry interval preserved beside unrelated prose |
| Privacy and portability | Current tracked source, examples, docs and review notes inspected for embedded operational data |

## Limits
Synthetic fixtures cannot establish compatibility with every future provider format. Content inspection and pattern checks are not a guarantee of universal secret detection or removal of earlier published copies.

SWARM-RECEIPT: APPROVED · 5/5 dimensions · 0 findings (0 major) · code-review/REVIEW-2026-09-25-AUTH-BOUNDARIES.md
