"""AnthropicProvider — mocked SDK, no real API calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from patchwright.core.llm import LLMConfigError, LLMRefusal, LLMResponseError
from patchwright.models.triage import TriageDisposition, TriagePacket
from patchwright.providers.anthropic_provider import AnthropicProvider


def _packet() -> TriagePacket:
    return TriagePacket(
        case_id="c",
        summary="x",
        claim_type="x",
        confidence=0.5,
        disposition=TriageDisposition.ADVANCE,
        rationale="x",
    )


def test_missing_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with (
        patch("keyring.get_password", return_value=None),
        pytest.raises(LLMConfigError, match="Secret"),
    ):
        AnthropicProvider().complete(system="s", user="u")


def test_parsed_call_returns_pydantic_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    fake_response = SimpleNamespace(stop_reason="end_turn", parsed_output=_packet())
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("anthropic.Anthropic", return_value=fake_client),
    ):
        provider = AnthropicProvider()
        out = provider.complete(system="sys", user="usr", response_schema=TriagePacket)
        assert isinstance(out, TriagePacket)
        assert out.disposition is TriageDisposition.ADVANCE
        assert fake_client.messages.parse.called


def test_text_call_returns_concatenated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    fake_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="text", text="hello "),
            SimpleNamespace(type="text", text="world"),
        ],
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("anthropic.Anthropic", return_value=fake_client),
    ):
        out = AnthropicProvider().complete(system="s", user="u")
        assert out == "hello world"


def test_refusal_maps_to_llm_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    fake_response = SimpleNamespace(
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber", explanation="declined"),
        parsed_output=None,
        content=[],
    )
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("anthropic.Anthropic", return_value=fake_client),
        pytest.raises(LLMRefusal, match="cyber"),
    ):
        AnthropicProvider().complete(system="s", user="u", response_schema=TriagePacket)


def test_empty_parsed_output_raises_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    fake_response = SimpleNamespace(stop_reason="max_tokens", parsed_output=None)
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = fake_response
    with (
        patch("keyring.get_password", return_value=None),
        patch("anthropic.Anthropic", return_value=fake_client),
        pytest.raises(LLMResponseError, match="no parsed_output"),
    ):
        AnthropicProvider().complete(system="s", user="u", response_schema=TriagePacket)
