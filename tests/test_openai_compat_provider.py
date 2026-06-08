"""OpenAICompatProvider — mocked SDK."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from patchwright.core.llm import LLMConfigError, LLMRefusal, LLMResponseError
from patchwright.models.triage import TriageDisposition, TriagePacket
from patchwright.providers.openai_compat import OpenAICompatProvider


def _packet() -> TriagePacket:
    return TriagePacket(
        case_id="c",
        summary="x",
        claim_type="x",
        confidence=0.5,
        disposition=TriageDisposition.ADVANCE,
        rationale="x",
    )


def test_local_endpoint_works_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake_choice = SimpleNamespace(message=SimpleNamespace(parsed=_packet()), finish_reason="stop")
    fake_response = SimpleNamespace(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("openai.OpenAI", return_value=fake_client) as openai_ctor,
    ):
        provider = OpenAICompatProvider(base_url="http://localhost:11434/v1", model="llama3")
        out = provider.complete(system="s", user="u", response_schema=TriagePacket)
        assert isinstance(out, TriagePacket)
        kwargs = openai_ctor.call_args.kwargs
        assert kwargs["api_key"] == "patchwright-local"
        assert kwargs["base_url"] == "http://localhost:11434/v1"


def test_remote_without_key_or_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with (
        patch("keyring.get_password", return_value=None),
        pytest.raises(LLMConfigError, match="needs either"),
    ):
        OpenAICompatProvider().complete(system="s", user="u")


def test_remote_with_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_choice = SimpleNamespace(
        message=SimpleNamespace(content="hi", parsed=None), finish_reason="stop"
    )
    fake_response = SimpleNamespace(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("openai.OpenAI", return_value=fake_client),
    ):
        out = OpenAICompatProvider().complete(system="s", user="u")
        assert out == "hi"


def test_empty_content_raises_response_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_choice = SimpleNamespace(
        message=SimpleNamespace(content="", parsed=None), finish_reason="stop"
    )
    fake_response = SimpleNamespace(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("openai.OpenAI", return_value=fake_client),
        pytest.raises(LLMResponseError, match="empty content"),
    ):
        OpenAICompatProvider().complete(system="s", user="u")


# --------------------------------------------------------------------------- fix #8: content_filter → LLMRefusal


def test_content_filter_raises_llm_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """finish_reason='content_filter' must raise LLMRefusal, not LLMResponseError."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_choice = SimpleNamespace(
        message=SimpleNamespace(parsed=None), finish_reason="content_filter"
    )
    fake_response = SimpleNamespace(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("openai.OpenAI", return_value=fake_client),
        pytest.raises(LLMRefusal, match="content_filter"),
    ):
        OpenAICompatProvider().complete(system="s", user="u", response_schema=_packet().__class__)


def test_stop_with_null_parsed_raises_response_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: finish_reason='stop' with parsed=None is a parse failure, not a refusal."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_choice = SimpleNamespace(
        message=SimpleNamespace(parsed=None), finish_reason="stop"
    )
    fake_response = SimpleNamespace(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("openai.OpenAI", return_value=fake_client),
        pytest.raises(LLMResponseError, match="no parsed message"),
    ):
        OpenAICompatProvider().complete(system="s", user="u", response_schema=_packet().__class__)
