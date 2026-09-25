"""All dispatch tests use synthetic local adapters and disposable state."""

import json
from pathlib import Path
import sys

import pytest


FAKE = """import argparse, json, os, signal, subprocess, sys, time
from pathlib import Path
p = argparse.ArgumentParser(add_help=False)
p.add_argument('--behavior', default=os.environ.get('TEST_BEHAVIOR', 'success'))
p.add_argument('--prompt-file')
p.add_argument('--model')
p.add_argument('--cwd')
args, rest = p.parse_known_args()
prompt = Path(args.prompt_file).read_text() if args.prompt_file else sys.stdin.read()
behavior = args.behavior
if behavior == 'timeout-quota':
    print(json.dumps({'type':'llm_run_error','kind':'quota'}), flush=True)
capture = os.environ.get('TEST_CAPTURE')
if capture:
    with open(capture, 'a') as fh:
        fh.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'prompt': prompt,
          'prompt_file': args.prompt_file,
          'mode': (Path(args.prompt_file).stat().st_mode & 0o777) if args.prompt_file else None,
          'keys_present': [k for k in ['ANTHROPIC_API_KEY','CODEX_API_KEY','OPENAI_API_KEY','CLAUDECODE'] if k in os.environ],
          'pid': os.getpid(), 'engine': os.environ.get('LLM_RUN_ENGINE')}) + '\\n')
behavior = args.behavior
if behavior in ['quota', 'auth', 'rejected_model']:
    print(json.dumps({'type':'llm_run_error','kind':behavior,'retry_after_seconds':60,'message':prompt}))
    sys.exit(1)
if behavior == 'provider-quota':
    print(json.dumps({'type':'error','error':{'type':'rate_limit_error','message':'capacity exhausted'}}))
    sys.exit(1)
if behavior == 'provider-auth':
    print(json.dumps({'type':'error','error':{'type':'authentication_error','message':'provider rejected credentials'}}))
    sys.exit(1)
if behavior == 'task-auth-prose':
    print(json.dumps({'type':'result','is_error':True,'result':'Not logged in. Please run /login'}))
    print('Error: Unauthorized response from local API')
    sys.exit(17)
if behavior == 'error':
    print('ValueError: task discusses authentication 401, 429 and rate limit tests')
    sys.exit(17)
if behavior == 'rejection-success':
    print(json.dumps({'type':'error','error':{'type':'rate_limit_error','message':'fixture'}}))
    sys.exit(0)
if behavior in ['exit-124', 'exit-130', 'signal-term']:
    print(json.dumps({'type':'llm_run_error','kind':'quota'}), flush=True)
    if behavior == 'signal-term':
        os.kill(os.getpid(), signal.SIGTERM)
    sys.exit(int(behavior.split('-')[1]))
if behavior == 'nested-error':
    print('{'+chr(10)+'"type":"result", "content":['+chr(10)+'{"type":"llm_run_error","kind":"quota"}'+chr(10)+']}')
    sys.exit(17)
if behavior == 'stderr-quota':
    sys.stdout.write('task progress')
    sys.stderr.write(json.dumps({'type':'llm_run_error','kind':'quota'}))
    sys.exit(1)
if behavior == 'usage-overflow':
    print('{"type":"result","usage":{"input_tokens":1e999,"output_tokens":2}}')
    sys.exit(0)
if behavior == 'grandchild-ignore-term':
    code = "import os, signal, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); p=Path(os.environ['TEST_CAPTURE']+'.worker'); p.write_text(str(os.getpid())); time.sleep(30)"
    subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(30)
if behavior in ['timeout', 'timeout-quota']:
    time.sleep(30)
if behavior == 'ignore-term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
if behavior == 'binary':
    sys.stdout.buffer.write(b'\\xff\\xfe')
    sys.exit(0)
print(json.dumps({'type':'result','result':prompt,'usage':{'input_tokens':4,'output_tokens':2}}))
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LLM_RUN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv("LLM_RUN_CONFIG", raising=False)
    monkeypatch.delenv("TEST_BEHAVIOR", raising=False)
    monkeypatch.delenv("TEST_CAPTURE", raising=False)
    return tmp_path


@pytest.fixture
def setup_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    adapter = tmp_path / "fake.py"
    adapter.write_text(FAKE)
    capture = tmp_path / "capture.jsonl"
    monkeypatch.setenv("TEST_CAPTURE", str(capture))

    def make(first="success", second="success", *, builtin=None):
        config = tmp_path / "policy.toml"
        text = '[lanes.default]\nchain=["first", "second"]\n'
        for name, behavior in [("first", first), ("second", second)]:
            text += f"\n[engines.{name}]\n"
            if builtin:
                executable = tmp_path / ("fake-" + name)
                executable.write_text(f"#!{sys.executable}\n" + FAKE)
                executable.chmod(0o755)
                text += f'adapter="{builtin}"\nbinary={json.dumps(str(executable))}\n'
            else:
                text += (
                    "command="
                    + json.dumps([sys.executable, str(adapter), "--behavior", behavior])
                    + "\n"
                )
        config.write_text(text)
        monkeypatch.setenv("LLM_RUN_CONFIG", str(config))
        return config, capture

    return make
