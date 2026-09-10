import json

import pytest

from llm_run.signatures import classify_output, parse_reset_epoch
from llm_run.scrub import scrub
from llm_run.usage import parse_tokens


@pytest.mark.parametrize(
    "text",
    [
        "assert status == 429",
        "HTTP 401 test case failed",
        "authentication test failed",
        "ValueError: rate limit is not configured",
        "models/config.yaml not found",
        "unknown model in a training dataset",
        "the documentation says you've hit your limit",
        json.dumps({"type": "result", "error": {"type": "rate_limit_error"}}),
        json.dumps(
            {"type": "assistant", "message": {"error": {"type": "rate_limit_error"}}}
        ),
        json.dumps({"type": "error", "error": "rate_limit_error"}),
        json.dumps({"type": "llm_run_error", "kind": "unexpected"}),
        json.dumps(
            {"type": "error", "error": {"type": "invalid_request_error", "message": 42}}
        ),
        json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "invalid temperature",
                },
            }
        ),
    ],
)
def test_task_errors_do_not_look_like_provider_failures(text):
    assert classify_output(text) is None


@pytest.mark.parametrize(
    "error_type,expected",
    [
        ("rate_limit_error", "quota"),
        ("quota_exceeded", "quota"),
        ("authentication_error", "auth"),
        ("invalid_api_key", "auth"),
        ("model_not_found", "rejected_model"),
    ],
)
def test_structured_provider_errors(error_type, expected):
    text = json.dumps(
        {
            "type": "error",
            "error": {"type": error_type, "message": "sample-model not found"},
        }
    )
    assert classify_output("ERROR: " + text, "sample-model") == expected
    assert classify_output(text) == expected


def test_rejected_model_requires_appropriate_message_and_matching_pin():
    text = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "The 'sample-model' model is not supported when using this account.",
            },
        }
    )
    assert classify_output(text, "sample-model") == "rejected_model"
    assert classify_output(text, "other-model") is None
    assert classify_output(text) == "rejected_model"
    assert (
        classify_output("ERROR: You've hit your usage limit. Try again later.")
        == "quota"
    )
    assert classify_output("Not logged in. Please run /login") == "auth"
    assert classify_output("OAuth session expired. Please run /login") == "auth"


def test_large_noise_retains_terminal_error_and_bounds_scan():
    text = "x" * 100_000 + '\n{"type":"error","error":{"type":"authentication_error"}}'
    assert classify_output(text) == "auth"


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("try again in 2 hours", 7200),
        ("try again in 1.5 hours", 5400),
        ("try again after 10 minutes", 600),
        ("try again in 2 days", 172800),
        ("no reset information", 100),
        ("try again in 999999999 days", 100),
        (
            json.dumps(
                {"type": "llm_run_error", "kind": "quota", "retry_after_seconds": 30}
            ),
            30,
        ),
        (
            json.dumps(
                {"type": "llm_run_error", "kind": "quota", "retry_after_seconds": -1}
            ),
            100,
        ),
    ],
)
def test_reset_fallback_and_units(text, seconds):
    assert parse_reset_epoch(text, 100, now=1000) == 1000 + seconds


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"usage":{"input_tokens":3,"output_tokens":5}}', {"in": 3, "out": 5}),
        ('{"token_usage":{"inputTokens":2,"outputTokens":7}}', {"in": 2, "out": 7}),
        ('[{"usage":{"input_tokens":2}}]', {"in": 2, "out": 0}),
        ('log\n{broken\n{"usage":{"output_tokens":4}}', {"in": 0, "out": 4}),
        ('{"usage":{"input_tokens":"invalid"}}', None),
        ('{"usage":{"other":1}}', None),
        ('{"usage":false}', None),
        ("", None),
        ("ordinary output", None),
    ],
)
def test_usage_counter_shapes(text, expected):
    assert parse_tokens(text)[0] == expected


def test_scrub_synthetic_token_shapes():
    for secret in [
        "sk-ant-" + "DEMO" * 10,
        "sk-" + "DEMO" * 10,
        "eyJ" + "a" * 22 + ".payload.signature",
    ]:
        assert secret not in scrub(secret)
    assert (
        scrub("Authorization: Bearer example-marker")
        == "Authorization: Bearer <redacted>"
    )
    assert (
        scrub("CLAUDE_CODE_OAUTH_TOKEN=example-marker")
        == "CLAUDE_CODE_OAUTH_TOKEN=<redacted>"
    )


@pytest.mark.parametrize("indent", [None, 0, 2])
@pytest.mark.parametrize("outer", ["result", "assistant"])
def test_complete_envelope_does_not_promote_nested_error(indent, outer):
    obj = {"type": outer, "content": [{"type": "llm_run_error", "kind": "quota"}]}
    assert classify_output(json.dumps(obj, indent=indent)) is None
    assert classify_output(json.dumps([obj], indent=indent)) is None


def test_truncated_pretty_envelope_is_not_jsonl_error():
    assert (
        classify_output(
            '{\n"type":"result", "content":[\n{"type":"llm_run_error","kind":"quota"}\n'
        )
        is None
    )


@pytest.mark.parametrize(
    "record",
    [
        {"type": "error", "message": "You've hit your usage limit. Try again later."},
        {
            "type": "turn.failed",
            "error": {"message": "You've hit your usage limit. Try again later."},
        },
    ],
)
def test_codex_top_level_error_messages(record):
    assert (
        classify_output(
            json.dumps({"type": "thread.started"}) + "\n" + json.dumps(record)
        )
        == "quota"
    )


@pytest.mark.parametrize(
    "value", ["1e999", str(2**63), "9" * 5000, "true", "-1", "1.5", '"1e999"']
)
def test_untrusted_token_counters_never_raise(value):
    text = '{"usage":{"input_tokens":' + value + "}}"
    assert parse_tokens(text)[0] is None


def test_huge_reset_and_deep_json_do_not_raise():
    text = json.dumps(
        {"type": "llm_run_error", "kind": "quota", "retry_after_seconds": 10**400}
    )
    assert parse_reset_epoch(text, 100, now=1000) == 1100
    text = "[" * 2000 + "0" + "]" * 2000
    assert parse_tokens(text)[0] is None
    assert classify_output(text) is None
