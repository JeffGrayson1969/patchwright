"""AnthropicProvider — Claude via the official Anthropic SDK.

Defaults to claude-opus-4-7 (per claude-api skill guidance: always use the
latest Opus for high-value calls unless the operator explicitly downgrades).
Adaptive thinking is on by default at effort='high' — triage benefits from
reasoning about whether a report is real, novel, and exploitable.

Structured outputs use client.messages.parse() with a Pydantic schema; the
SDK handles JSON-schema generation, retry on schema mismatch, and parsed-output
extraction.

Errors are mapped to typed LLM* exceptions so agents do not depend on the
anthropic package's exception classes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError

from patchwright.core.llm import LLMConfigError, LLMRefusal, LLMResponseError
from patchwright.core.secrets import SecretNotFound, get_secret

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_API_KEY_NAME = "ANTHROPIC_API_KEY"

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass
class AnthropicProvider:
    """LLMProvider backed by the Anthropic SDK."""

    name: str = "anthropic"
    model: str = DEFAULT_MODEL
    effort: EffortLevel = "high"
    use_adaptive_thinking: bool = True
    api_key_env: str = DEFAULT_API_KEY_NAME
    _client: Any = field(default=None, init=False, repr=False)

    def _get_client(self) -> Any:
        """Lazy-init the SDK client so test code can construct the dataclass
        without an API key present."""
        if self._client is not None:
            return self._client
        try:
            import anthropic  # noqa: PLC0415 - lazy: only import when provider used
        except ImportError as exc:  # pragma: no cover - hard dep
            raise LLMConfigError("anthropic SDK not installed") from exc

        try:
            api_key = get_secret(self.api_key_env, required=True)
        except SecretNotFound as exc:
            raise LLMConfigError(str(exc)) from exc

        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[T] | None = None,
        max_output_tokens: int = 8192,
    ) -> T | str:
        client = self._get_client()
        import anthropic  # noqa: PLC0415 - lazy; _get_client already validated availability

        thinking = {"type": "adaptive"} if self.use_adaptive_thinking else {"type": "disabled"}
        output_config: dict[str, object] = {"effort": self.effort}

        try:
            if response_schema is not None:
                response = client.messages.parse(
                    model=self.model,
                    max_tokens=max_output_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=response_schema,
                    thinking=thinking,
                    output_config=output_config,
                )
                if response.stop_reason == "refusal":
                    raise LLMRefusal(_format_refusal(response))
                parsed = response.parsed_output
                if parsed is None:
                    raise LLMResponseError(
                        "Anthropic response had no parsed_output; "
                        f"stop_reason={response.stop_reason!r}"
                    )
                return cast("T", parsed)

            response = client.messages.create(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking=thinking,
                output_config=output_config,
            )
            if response.stop_reason == "refusal":
                raise LLMRefusal(_format_refusal(response))
            text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
            if not text_blocks:
                raise LLMResponseError(
                    f"Anthropic response had no text blocks; stop_reason={response.stop_reason!r}"
                )
            return "".join(text_blocks)

        except anthropic.AuthenticationError as exc:
            raise LLMConfigError(f"Anthropic auth failed: {exc}") from exc
        except anthropic.BadRequestError as exc:
            raise LLMResponseError(f"Anthropic bad request: {exc}") from exc
        except ValidationError as exc:
            raise LLMResponseError(f"Anthropic response schema mismatch: {exc}") from exc


def _format_refusal(response: object) -> str:
    details = getattr(response, "stop_details", None)
    if details is None:
        return "Anthropic refused (no stop_details)"
    category = getattr(details, "category", "unknown")
    explanation = getattr(details, "explanation", "")
    return f"Anthropic refused ({category}): {explanation}"
