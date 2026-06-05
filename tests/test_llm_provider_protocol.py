"""Test that the three concrete providers satisfy the LLMProvider Protocol."""

from __future__ import annotations

from patchwright.core.llm import LLMProvider
from patchwright.providers.anthropic_provider import AnthropicProvider
from patchwright.providers.mcp_sampling import MCPSamplingProvider
from patchwright.providers.openai_compat import OpenAICompatProvider


def test_anthropic_satisfies_protocol() -> None:
    assert isinstance(AnthropicProvider(), LLMProvider)


def test_openai_compat_satisfies_protocol() -> None:
    assert isinstance(OpenAICompatProvider(), LLMProvider)


def test_mcp_sampling_satisfies_protocol() -> None:
    assert isinstance(MCPSamplingProvider(), LLMProvider)
