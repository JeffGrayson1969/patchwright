"""MCPSamplingProvider stub raises LLMConfigError until M7."""

from __future__ import annotations

import pytest

from patchwright.core.llm import LLMConfigError
from patchwright.providers.mcp_sampling import MCPSamplingProvider


def test_stub_raises_with_clear_message() -> None:
    with pytest.raises(LLMConfigError, match="M7"):
        MCPSamplingProvider().complete(system="s", user="u")
