import logging

from app.core.logging import RedactingFilter, redact, register_secret


def test_key_value_patterns_are_redacted():
    out = redact('api_key=abc123supersecret and "password": "hunter2hunter2"')
    assert "abc123supersecret" not in out
    assert "hunter2hunter2" not in out


def test_bearer_and_sk_tokens_are_redacted():
    out = redact("Authorization: Bearer abc.def-123 and sk-live_ABCDEFGH12345")
    assert "abc.def-123" not in out
    assert "sk-live_ABCDEFGH12345" not in out


def test_registered_secret_is_scrubbed_anywhere():
    register_secret("my-very-private-value")
    assert "my-very-private-value" not in redact("oops my-very-private-value leaked")


def test_filter_scrubs_formatted_args():
    register_secret("another-secret-9876")
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "key is %s", ("another-secret-9876",), None
    )
    RedactingFilter().filter(record)
    assert "another-secret-9876" not in record.getMessage()