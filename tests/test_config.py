import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_parses_comma_separated_lists():
    settings = make_settings(
        telegram_allowed_user_ids="123, 456",
        ai_provider_order="groq,ollama",
    )

    assert settings.telegram_allowed_user_ids == [123, 456]
    assert settings.ai_provider_order == ["groq", "ollama"]


def test_empty_values_mean_not_configured():
    settings = make_settings(telegram_allowed_user_ids="", telegram_bot_token="", gemini_api_key="")

    assert settings.telegram_allowed_user_ids == []
    assert settings.telegram_bot_token is None
    assert settings.gemini_api_key is None


def test_rejects_unknown_ai_provider():
    with pytest.raises(ValidationError):
        make_settings(ai_provider_order="gemini,openai")


def test_rejects_unknown_timezone():
    with pytest.raises(ValidationError):
        make_settings(timezone="Europe/Atlantis")


def test_default_timezone_is_madrid():
    assert make_settings().tz.key == "Europe/Madrid"


def test_telegram_requires_token_and_allowlist_when_enabled():
    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TOKEN"):
        make_settings(telegram_enabled=True, telegram_allowed_user_ids="123")
    with pytest.raises(ValidationError, match="TELEGRAM_ALLOWED_USER_IDS"):
        make_settings(telegram_enabled=True, telegram_bot_token="123456:abc")


def test_database_url_escapes_password_and_hides_it_in_repr():
    settings = make_settings(postgres_password="p@ss/word:1", postgres_host="db")
    url = settings.database_url

    assert url.password == "p@ss/word:1"
    assert url.host == "db"
    assert "p@ss" not in repr(url)
    assert "p@ss" not in repr(settings)


def test_secret_values_lists_only_configured_secrets():
    settings = make_settings(postgres_password="dbsecret", groq_api_key="gsk_secret")

    assert sorted(settings.secret_values()) == ["dbsecret", "gsk_secret"]
