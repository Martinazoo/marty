import json
import logging
import sys

from app.core.logging import REDACTED, ConsoleFormatter, JsonFormatter, SecretRedactor

TELEGRAM_TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def _record(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, None)
    record.__dict__.update(extra)
    return record


def test_redacts_configured_secrets():
    redactor = SecretRedactor(["hunter22"])

    assert redactor.redact("password=hunter22") == f"password={REDACTED}"


def test_redacts_telegram_tokens_without_configuration():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

    assert TELEGRAM_TOKEN not in SecretRedactor().redact(url)


def test_ignores_trivially_short_secrets():
    assert SecretRedactor(["ab"]).redact("abc") == "abc"


def test_json_formatter_redacts_message_args_and_extras():
    formatter = JsonFormatter(SecretRedactor(["s3cr3t-key"]))
    record = _record("calling with %s", "s3cr3t-key", detail={"key": "s3cr3t-key"}, count=3)

    payload = json.loads(formatter.format(record))

    assert payload["msg"] == f"calling with {REDACTED}"
    assert payload["detail"] == {"key": REDACTED}
    assert payload["count"] == 3
    assert payload["level"] == "INFO"


def test_console_formatter_shows_extras_redacted():
    formatter = ConsoleFormatter(SecretRedactor(["s3cr3t-key"]))
    record = _record("provider failed", provider="gemini", key="s3cr3t-key")

    output = formatter.format(record)

    assert output.endswith(f"provider failed [provider=gemini key={REDACTED}]")


def test_json_formatter_redacts_exception_text():
    formatter = JsonFormatter(SecretRedactor(["s3cr3t-key"]))
    try:
        raise RuntimeError("auth failed for s3cr3t-key")
    except RuntimeError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())

    output = formatter.format(record)

    assert "s3cr3t-key" not in output
    assert "RuntimeError" in output
