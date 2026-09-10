# Development instructions

- Keep the package independent of private infrastructure and configuration.
- Runtime dependencies remain Python standard library only.
- Use synthetic adapters in tests; do not call paid providers for routine checks.
- Run `bash scripts/verify-quality.sh` before proposing a release.
- Test error classification with positive and negative controls. Successful
  output, ordinary task errors, timeouts, and interrupts must not cause retries.
- Do not persist prompt text or raw error output in metadata stores.
- Work on a feature branch and use a pull request for changes.
