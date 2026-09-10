"""Run a complete offline fallback demonstration with disposable state."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    adapter = Path(__file__).with_name("adapter.py").resolve()
    with tempfile.TemporaryDirectory(prefix="llm-run-demo-") as directory:
        root = Path(directory)
        config = root / "demo.toml"
        config.write_text(
            '[lanes.default]\nchain = ["busy", "ready"]\n\n'
            "[engines.busy]\ncommand = "
            + json.dumps([sys.executable, str(adapter), "--behavior", "quota"])
            + "\n\n"
            "[engines.ready]\ncommand = "
            + json.dumps([sys.executable, str(adapter)])
            + "\n",
            encoding="utf-8",
        )
        env = dict(os.environ, LLM_RUN_STATE_DIR=str(root / "state"))
        base = [sys.executable, "-m", "llm_run", "--config", str(config)]
        for label in ["First run: fallback", "Second run: cooldown skip"]:
            print(f"\n{label}", flush=True)
            subprocess.run(
                base + ["--prompt", "Hello from the offline demo.", "--json"],
                env=env,
                check=True,
            )
        print("\nLocal execution report", flush=True)
        subprocess.run(base + ["report", "--since", "24h"], env=env, check=True)


if __name__ == "__main__":
    main()
