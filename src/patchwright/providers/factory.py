"""Build an LLMProvider from a PatchwrightConfig.

Single chokepoint for provider selection. Enforces `embargo.mode='strict'`
(R2 / T4 mitigation): when strict, refuses any provider whose endpoint is
not in the configured local_hosts allowlist.

This is the operator-facing factory. Agents that need a provider should
generally accept one as a constructor arg (testability), and a top-level
runner builds it once from config.
"""

from __future__ import annotations

from patchwright.core.config import EmbargoConfig, LLMConfig, PatchwrightConfig
from patchwright.core.llm import LLMConfigError, LLMProvider
from patchwright.providers.anthropic_provider import AnthropicProvider
from patchwright.providers.mcp_sampling import MCPSamplingProvider
from patchwright.providers.openai_compat import OpenAICompatProvider


def build_cross_checker(config: PatchwrightConfig) -> LLMProvider:
    """Construct the cross-checker's LLMProvider per the cross_checker config section.

    OSS single-provider mode (cross_checker.provider is None): reuses the primary
    provider type but with the cross-checker's model override applied. The load-bearing
    distinction is the different system prompt (skeptic framing), not temperature —
    the LLMProvider Protocol does not expose temperature so temperature_delta is
    reserved for future provider implementations that do.

    Multi-provider mode: if cross_checker.provider is set, builds a fresh provider
    of that type, independent of the primary. No consensus aggregation — that's Shield
    (PRD §12.2).
    """
    cc = config.cross_checker
    _enforce_embargo(config.embargo, config.llm)

    # Determine which provider type to use.
    provider_name = cc.provider if cc.provider is not None else config.llm.provider
    # Determine which model to use (cross-checker default is provider's own default).
    model_override = cc.model  # None = provider default

    # Build a synthetic LLMConfig for the cross-checker provider.
    cross_llm = LLMConfig(
        provider=provider_name,
        model=model_override,
        base_url=config.llm.base_url,  # carry base_url for openai_compat continuity
        effort=config.llm.effort,
    )
    return _build(cross_llm)


def provider_from_config(config: PatchwrightConfig) -> LLMProvider:
    """Instantiate the configured LLMProvider, enforcing embargo policy.

    Raises:
        LLMConfigError: when embargo.mode='strict' and the requested provider
            is not local, or when required config fields (e.g. base_url for
            openai_compat in strict mode) are missing.
    """
    _enforce_embargo(config.embargo, config.llm)
    return _build(config.llm)


def _enforce_embargo(embargo: EmbargoConfig, llm: LLMConfig) -> None:
    """Refuse non-local providers when embargo.mode == 'strict'."""
    if embargo.mode != "strict":
        return

    # Anthropic and MCPSampling are inherently non-local in the current
    # implementations — they call out to the public API or rely on a host
    # process. Both are refused in strict mode.
    if llm.provider == "anthropic":
        raise LLMConfigError(
            "embargo.mode='strict' refuses provider 'anthropic' (calls the public API). "
            "Use provider='openai_compat' with base_url pointing to a local "
            f"endpoint (one of {sorted(embargo.local_hosts)})."
        )

    if llm.provider == "mcp_sampling":
        raise LLMConfigError(
            "embargo.mode='strict' refuses provider 'mcp_sampling' "
            "(host LLM may be remote). Use provider='openai_compat' with "
            f"base_url in {sorted(embargo.local_hosts)}."
        )

    # openai_compat is allowed only if base_url is set AND resolves to a
    # local host per the allowlist.
    if llm.provider == "openai_compat":
        if not llm.base_url:
            raise LLMConfigError(
                "embargo.mode='strict' requires llm.base_url to be set when "
                "provider='openai_compat'."
            )
        if not _host_in(llm.base_url, embargo.local_hosts):
            raise LLMConfigError(
                f"embargo.mode='strict': llm.base_url={llm.base_url!r} is not in "
                f"local_hosts={sorted(embargo.local_hosts)}."
            )


def _host_in(url: str, allowlist: list[str]) -> bool:
    from urllib.parse import urlparse  # noqa: PLC0415 - local import to keep module surface small

    host = urlparse(url).hostname or ""
    return host in allowlist


def _build(llm: LLMConfig) -> LLMProvider:
    if llm.provider == "anthropic":
        kwargs: dict[str, object] = {"effort": llm.effort}
        if llm.model:
            kwargs["model"] = llm.model
        return AnthropicProvider(**kwargs)  # type: ignore[arg-type]

    if llm.provider == "openai_compat":
        kwargs = {}
        if llm.model:
            kwargs["model"] = llm.model
        if llm.base_url:
            kwargs["base_url"] = llm.base_url
        return OpenAICompatProvider(**kwargs)  # type: ignore[arg-type]

    if llm.provider == "mcp_sampling":
        return MCPSamplingProvider()

    raise LLMConfigError(f"unknown llm.provider: {llm.provider!r}")  # pragma: no cover


__all__ = ["build_cross_checker", "provider_from_config"]
