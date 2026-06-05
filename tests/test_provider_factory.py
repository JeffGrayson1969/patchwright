"""Provider factory + embargo enforcement (M5-config exit criterion).

The exit criterion from phase1-work-plan.md is:

> embargo_mode: strict hard-fails any non-local LLM call (test).
"""

from __future__ import annotations

import pytest

from patchwright.core.config import PatchwrightConfig
from patchwright.core.llm import LLMConfigError
from patchwright.providers.anthropic_provider import AnthropicProvider
from patchwright.providers.factory import provider_from_config
from patchwright.providers.mcp_sampling import MCPSamplingProvider
from patchwright.providers.openai_compat import OpenAICompatProvider


def _config(**overrides: object) -> PatchwrightConfig:
    return PatchwrightConfig.model_validate(overrides)


# --------------------------------------------------------------------------- normal mode


def test_normal_mode_anthropic() -> None:
    provider = provider_from_config(_config())
    assert isinstance(provider, AnthropicProvider)
    assert provider.effort == "high"


def test_normal_mode_anthropic_with_model_override() -> None:
    provider = provider_from_config(
        _config(llm={"provider": "anthropic", "model": "claude-haiku-4-5", "effort": "medium"})
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-haiku-4-5"
    assert provider.effort == "medium"


def test_normal_mode_openai_compat() -> None:
    provider = provider_from_config(
        _config(
            llm={
                "provider": "openai_compat",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
            }
        )
    )
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "https://api.openai.com/v1"
    assert provider.model == "gpt-4o"


def test_normal_mode_mcp_sampling() -> None:
    provider = provider_from_config(_config(llm={"provider": "mcp_sampling"}))
    assert isinstance(provider, MCPSamplingProvider)


# --------------------------------------------------------------------------- strict mode (R2 / T4)


def test_strict_mode_refuses_anthropic() -> None:
    with pytest.raises(LLMConfigError, match="refuses provider 'anthropic'"):
        provider_from_config(_config(llm={"provider": "anthropic"}, embargo={"mode": "strict"}))


def test_strict_mode_refuses_mcp_sampling() -> None:
    with pytest.raises(LLMConfigError, match="refuses provider 'mcp_sampling'"):
        provider_from_config(_config(llm={"provider": "mcp_sampling"}, embargo={"mode": "strict"}))


def test_strict_mode_refuses_openai_compat_without_base_url() -> None:
    with pytest.raises(LLMConfigError, match=r"requires llm\.base_url"):
        provider_from_config(_config(llm={"provider": "openai_compat"}, embargo={"mode": "strict"}))


def test_strict_mode_refuses_openai_compat_with_remote_base_url() -> None:
    with pytest.raises(LLMConfigError, match="not in local_hosts"):
        provider_from_config(
            _config(
                llm={"provider": "openai_compat", "base_url": "https://api.openai.com/v1"},
                embargo={"mode": "strict"},
            )
        )


def test_strict_mode_allows_openai_compat_with_localhost() -> None:
    provider = provider_from_config(
        _config(
            llm={
                "provider": "openai_compat",
                "base_url": "http://localhost:11434/v1",
                "model": "llama3",
            },
            embargo={"mode": "strict"},
        )
    )
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "http://localhost:11434/v1"


def test_strict_mode_respects_custom_local_hosts() -> None:
    provider = provider_from_config(
        _config(
            llm={
                "provider": "openai_compat",
                "base_url": "https://my-vllm.internal:8000/v1",
            },
            embargo={"mode": "strict", "local_hosts": ["my-vllm.internal"]},
        )
    )
    assert isinstance(provider, OpenAICompatProvider)


def test_strict_mode_127_loopback_allowed() -> None:
    provider = provider_from_config(
        _config(
            llm={"provider": "openai_compat", "base_url": "http://127.0.0.1:8000"},
            embargo={"mode": "strict"},
        )
    )
    assert isinstance(provider, OpenAICompatProvider)
