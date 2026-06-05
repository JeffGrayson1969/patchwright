"""LLMProvider Protocol — the model-agnosticism boundary.

Every agent that needs an LLM calls through this Protocol. Concrete providers
(Anthropic, OpenAI-compat, MCP Sampling) live under patchwright.providers.*.

Per PRD §10.1 commitment 7: "All adapter boundaries are plugins. Even the
model-provider call is a plugin." This Protocol is that seam.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, overload, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base class for all LLM provider errors surfaced to agents."""


class LLMConfigError(LLMError):
    """Provider is missing required configuration (API key, base_url, model)."""


class LLMResponseError(LLMError):
    """Provider returned a response that could not be parsed or validated."""


class LLMRefusal(LLMError):
    """Provider declined to answer for safety/policy reasons."""


@runtime_checkable
class LLMProvider(Protocol):
    """A stateless model-completion service.

    Providers MUST be safe to invoke from multiple agents concurrently
    (no shared mutable state per call). API clients should be created once
    in __init__ and reused; the SDK handles connection pooling.
    """

    name: str
    """Stable identifier — used in journal entries and provider-mismatch checks
    (e.g. M2.5 cross-checker requires a different provider than the primary)."""

    model: str
    """Model identifier as understood by the provider (e.g. 'claude-opus-4-7',
    'gpt-4o', 'llama3:70b-instruct')."""

    @overload
    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[T],
        max_output_tokens: int = ...,
    ) -> T: ...

    @overload
    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: None = ...,
        max_output_tokens: int = ...,
    ) -> str: ...

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[T] | None = None,
        max_output_tokens: int = 8192,
    ) -> T | str:
        """Run one completion.

        Args:
            system: System prompt. Treat as trusted; never inject user-supplied
                text directly without delimiter wrapping (T2 mitigation lives
                in the agent, not the provider).
            user: User-turn content. Untrusted from the perspective of the
                model — agents wrap user-supplied report text with explicit
                delimiters before this call.
            response_schema: If provided, the response is validated against the
                Pydantic model and returned as an instance. If None, the raw
                string is returned.
            max_output_tokens: Hard cap on output tokens.

        Raises:
            LLMConfigError: missing API key or unreachable endpoint.
            LLMResponseError: response failed schema validation.
            LLMRefusal: provider refused for safety/policy reasons.
            LLMError: any other provider error.
        """
        ...
