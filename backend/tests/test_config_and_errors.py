import httpx
import openai
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.rag.errors import GENERIC_MESSAGE, user_safe_message

_REQ = httpx.Request("POST", "http://llm.test")


def _status(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("secret upstream body", response=httpx.Response(code, request=_REQ), body=None)


# --- settings -----------------------------------------------------------------------


def test_blank_env_values_mean_unset() -> None:
    s = Settings(llm_base_url="", llm_api_key="  ", static_dir="", otel_exporter_otlp_endpoint="")
    assert s.llm_base_url is None and s.llm_api_key is None
    assert s.static_dir is None and s.otel_exporter_otlp_endpoint is None


def test_llm_key_falls_back_to_openai_key() -> None:
    assert Settings(llm_api_key=None, openai_api_key="sk-o").resolved_llm_api_key == "sk-o"
    assert Settings(llm_api_key="gsk-l", openai_api_key="sk-o").resolved_llm_api_key == "gsk-l"
    assert Settings(llm_api_key=None, openai_api_key=None).resolved_llm_api_key is None


def test_log_level_is_validated_and_normalised() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        Settings(log_level="loud")


def test_derived_values() -> None:
    s = Settings(max_upload_mb=2, cors_origins="http://a, http://b,")
    assert s.max_upload_bytes == 2 * 1024 * 1024
    assert s.cors_origin_list == ["http://a", "http://b"]


# --- user-safe errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (_status(openai.RateLimitError, 429), "rate-limited"),
        (openai.APITimeoutError(request=_REQ), "too long"),
        (openai.APIConnectionError(request=_REQ), "unreachable"),
        (_status(openai.AuthenticationError, 401), "misconfigured"),
        (_status(openai.PermissionDeniedError, 403), "misconfigured"),
        (_status(openai.InternalServerError, 500), "returned an error"),
        (OperationalError("SELECT secret", {}, Exception("host db:5432")), "knowledge base"),
        (ConnectionRefusedError("127.0.0.1:5432"), "knowledge base"),
        (TimeoutError(), "knowledge base"),
    ],
)
def test_user_safe_message_maps_known_failures(exc: BaseException, needle: str) -> None:
    message = user_safe_message(exc)
    assert needle in message
    assert "secret" not in message and "5432" not in message


def test_user_safe_message_never_echoes_unknown_errors() -> None:
    assert user_safe_message(RuntimeError("postgres://user:pw@db")) == GENERIC_MESSAGE
