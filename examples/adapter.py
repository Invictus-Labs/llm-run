"""A deterministic custom adapter. No credentials or network."""

import argparse
import json
from pathlib import Path
import sys

p = argparse.ArgumentParser()
p.add_argument("--behavior", choices=["quota", "success"], default="success")
p.add_argument("--model")
p.add_argument("--cwd")
p.add_argument("--prompt-file", required=True)
args = p.parse_args()
if args.behavior == "quota":
    print(
        json.dumps(
            {"type": "llm_run_error", "kind": "quota", "retry_after_seconds": 60}
        )
    )
    sys.exit(1)
prompt = Path(args.prompt_file).read_text(encoding="utf-8")
print(
    json.dumps(
        {
            "type": "result",
            "result": f"Local demo received {len(prompt)} characters.",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }
    )
)
