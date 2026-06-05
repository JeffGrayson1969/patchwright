"""OpenAICompatProvider — OpenAI SDK with configurable base_url.

Per PRD §A.3: "OpenAIProvider with base_url config; supports Groq, OpenRouter,
Together, Ollama, vLLM, Azure OpenAI, AWS Bedrock (via proxy) through the
single env var PATCHWRIGHT_LLM_BASE_URL."

This is the R1 (source code leaving network) mitigation lever — point at a
local Ollama or vLLM endpoint to keep prompts off the public internet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from patchwright.core.llm import LLMConfigError, LLMResponseError
from patchwright.core.secrets import SecretNotFound, get_secret

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_API_KEY_NAME = "OPENAI_API_KEY"
LOCAL_API_KEY_PLACEHOLDER = "patchwright-local"


@dataclass
class OpenAICompatProvider:
    """LLMProvider backed by the OpenAI SDK against any compatible endpoint.

    For local backends (Ollama, vLLM), set base_url and let api_key default to
    'patchwright-local' — most local servers don't validate it.
    """

    name: str = "openai_compat"
    model: str = "gpt-4o"
    base_url: str | None = None
    api_key_env: str = DEFAULT_API_KEY_NAME
    _client: Any = field(default=None, init=False, repr=False)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import openai  # noqa: PLC0415 - lazy: only import when provider used
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("openai SDK not installed") from exc

        api_key: str | None
        try:
            api_key = get_secret(self.api_key_env, required=False)
        except SecretNotFound:
            api_key = None

        if api_key is None:
            if self.base_url is None:
                raise LLMConfigError(
                    f"OpenAICompatProvider needs either {self.api_key_env} or base_url set"
                )
            api_key = LOCAL_API_KEY_PLACEHOLDER

        self._client = openai.OpenAI(api_key=api_key, base_url=self.base_url)
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
        import openai  # noqa: PLC0415 - lazy; _get_client already validated availability

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        try:
            if response_schema is not None:
                response = client.beta.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=response_schema,
                    max_completion_tokens=max_output_tokens,
                )
                choice = response.choices[0]
                parsed = choice.message.parsed
                if parsed is None:
                    raise LLMResponseError(
                        f"OpenAI response had no parsed message; "
                        f"finish_reason={choice.finish_reason!r}"
                    )
                return cast("T", parsed)

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_output_tokens,
            )
            content = response.choices[0].message.content
            if not content:
                raise LLMResponseError("OpenAI response had empty content")
            return cast("str", content)

        except openai.AuthenticationError as exc:
            raise LLMConfigError(f"OpenAI-compat auth failed: {exc}") from exc
        except openai.BadRequestError as exc:
            raise LLMResponseError(f"OpenAI-compat bad request: {exc}") from exc
        except ValidationError as exc:
            raise LLMResponseError(f"OpenAI-compat schema mismatch: {exc}") from exc
