"""MCPSamplingProvider — stub for the M7 MCP server.

Per PRD §A.3: when PatchWright runs as an MCP server (Mode B in §A.2), the
host (Claude Code, Cursor, Cline) provides its own LLM via MCP Sampling.
The agent calls into this provider and the MCP runtime routes the request
through to the host.

P1-Wave A scope: stub. The real implementation lives in M7 (`cli/serve.py`).
Until then, attempting to use this provider raises LLMConfigError to make
the missing-implementation explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from patchwright.core.llm import LLMConfigError

T = TypeVar("T", bound=BaseModel)


@dataclass
class MCPSamplingProvider:
    """Stub provider. Implemented in M7."""

    name: str = "mcp_sampling"
    model: str = "host-provided"

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[T] | None = None,
        max_output_tokens: int = 8192,
    ) -> T | str:
        raise LLMConfigError(
            "MCPSamplingProvider is a stub; full implementation lands in M7 "
            "(`patchwright serve --mcp`). For now, use AnthropicProvider or "
            "OpenAICompatProvider."
        )
