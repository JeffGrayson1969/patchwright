"""PatchwrightConfig — per-project configuration (FR-CF-1).

Loaded from `patchwright.yaml` in the project root (or any parent dir up
to the filesystem boundary). `patchwright init` writes a default one.

Sections:
- llm: which LLMProvider and model to use
- embargo: disclosure timer defaults + 'strict' mode that gates LLM choice
  (T4 / R2 mitigation — embargoed cases never call a non-local model)
- sandbox: backend selection (docker dev / gvisor prod)
- review: which FSM transitions require human approval
- conventions: per-project code style, test command, branch prefix —
  consumed by M2 patch generation in Wave B

This file does NOT load secrets — those live in OS keychain via core/secrets.

Plugin SDK config (FR-CF-2) is intentionally deferred to Wave C with M8
(same signing toolchain). When it lands, a `plugins:` section will be
added here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONFIG_FILENAME = "patchwright.yaml"


class ConfigError(Exception):
    """Raised when the config file is missing required fields or malformed."""


# --------------------------------------------------------------------------- sections


class LLMConfig(BaseModel):
    """Which model-provider to use and how to call it.

    Provider-specific settings (api_key_env, model defaults) live on the
    provider classes themselves; this is the operator-facing knob.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "openai_compat", "mcp_sampling"] = "anthropic"
    """Which patchwright.providers.* implementation to instantiate."""

    model: str | None = Field(
        default=None,
        description="Override the provider's default model. None = use provider default.",
    )

    base_url: str | None = Field(
        default=None,
        description=(
            "For openai_compat only. Point at Ollama / vLLM / Groq / OpenRouter etc. "
            "Required when embargo.mode='strict' selects this provider."
        ),
    )

    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    """Reasoning effort level — passed to providers that honor it."""


class EmbargoConfig(BaseModel):
    """Disclosure-embargo timing + strict-mode network gate.

    `mode='strict'` is the R2 mitigation (embargoed CVE data exposure):
    embargoed cases MUST NOT call any LLM whose endpoint is outside the
    `local_hosts` allowlist. Defaults match RFC-style "localhost" semantics.
    """

    model_config = ConfigDict(extra="forbid")

    default_days: int = Field(default=90, ge=1, le=730)
    """Standard embargo length in days. PRD §6.6 default."""

    critical_days: int = Field(default=14, ge=1, le=90)
    """Embargo for high-severity / actively-exploited issues."""

    mode: Literal["normal", "strict"] = "normal"
    """In 'strict' mode, the provider factory refuses any non-local LLM."""

    local_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1"],
        description=(
            "Hostnames considered 'local' for embargo.mode='strict'. base_url "
            "must resolve to one of these for the LLM call to be permitted."
        ),
    )


class SandboxConfig(BaseModel):
    """Backend selection for the M3 sandbox runner. gVisor lands in Wave B."""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["docker", "gvisor"] = "docker"
    """'docker' is the dev backend (M3-shim). 'gvisor' lands in M3-hard, Wave B."""

    network: Literal["none", "limited"] = "none"
    """Default-deny network access per NFR-S-2. 'limited' will support
    per-case allowlists when M3-hard ships."""


class ReviewConfig(BaseModel):
    """Which FSM transitions require human approval before advancing.

    PRD §3.1 commitment 3: 'human-in-the-loop checkpoint at every state
    transition by default'. This setting lets operators opt out of specific
    checkpoints (e.g. auto-advance TRIAGED→REPRODUCED if confidence > 0.9).
    """

    model_config = ConfigDict(extra="forbid")

    checkpoints: list[str] = Field(
        default_factory=lambda: ["TRIAGED", "PATCH_APPLIED", "AWAITING_REVIEW"],
        description="FSM state names that pause for human approval.",
    )


class ConventionsConfig(BaseModel):
    """Per-project conventions consumed by M2 patch generation."""

    model_config = ConfigDict(extra="forbid")

    code_style: Literal["ruff", "black", "none"] = "ruff"
    """Formatter to run on patched files before committing."""

    test_command: str = "pytest"
    """How to invoke the test suite (e.g. 'pytest -q', 'python -m pytest')."""

    test_image: str = "python:3.12-slim"
    """Container image the M2-pr.4 patch_apply agent runs `test_command` inside.
    Operator-overridable for non-Python projects or hardened bases."""

    branch_prefix: str = "patchwright/"
    """Prefix for feature branches the patch agent creates."""


class RepoConfig(BaseModel):
    """Git-host backend for the post-transition PR effect runner.

    Only 'github' is wired in P1 (AEG-422). GitLab / Bitbucket land in P2+
    behind the same RepoAdapter Protocol — when they do, this becomes a
    union via the plugin SDK (M5-plugin/M8, Wave C).
    """

    model_config = ConfigDict(extra="forbid")

    adapter: Literal["github"] = "github"
    """Which patchwright.adapters.repo_* implementation to instantiate."""

    default_base_branch: str = "main"
    """Default base for draft PRs. Per-case overrides come from PatchPlan."""


class CrossCheckerConfig(BaseModel):
    """OSS single-provider mode catches intent-mismatch attacks (e.g. wrong-CWE plans) and
    prompt-injection survival. Does NOT defend against a compromised model whose output
    distribution is itself the attack — full T9 mitigation requires Shield multi-provider
    mode (PRD §12.2)."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "openai_compat", "mcp_sampling"] | None = None
    """None = reuse primary provider; set to use a different provider backend."""

    model: str | None = None
    """Model override for the cross-checker. None = provider default."""

    # reserved — not wired; see Field description
    temperature_delta: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description=(
            "RESERVED — not wired. The LLMProvider Protocol does not expose temperature; "
            "this field has no effect in any current provider. "
            "Will be wired when the Protocol is extended."
        ),
    )

    system_prompt_style: Literal["skeptic"] = "skeptic"
    """Cross-checker prompt framing. 'skeptic' is the M2.5 default."""


# --------------------------------------------------------------------------- root


class PatchwrightConfig(BaseModel):
    """Top-level config. Every section has a sane default; the entire file
    is optional (an empty patchwright.yaml is valid and yields all defaults)."""

    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embargo: EmbargoConfig = Field(default_factory=EmbargoConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    conventions: ConventionsConfig = Field(default_factory=ConventionsConfig)
    cross_checker: CrossCheckerConfig = Field(default_factory=CrossCheckerConfig)
    repo: RepoConfig = Field(default_factory=RepoConfig)

    # ---------- I/O ----------

    @classmethod
    def load(cls, path: Path) -> PatchwrightConfig:
        """Load a config from a specific YAML file. Raises ConfigError on
        parse / validation failure."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigError(f"config file not found: {path}") from exc

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ConfigError(f"YAML parse error in {path}: {exc}") from exc

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError(f"config root must be a mapping, got {type(data).__name__}")

        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"invalid config in {path}: {exc}") from exc

    @classmethod
    def discover(cls, start: Path | None = None) -> PatchwrightConfig:
        """Walk up from `start` (default cwd) looking for patchwright.yaml.

        Returns the loaded config if found, or the all-defaults config if not.
        Stops at the filesystem root or at a `.git` directory (project boundary).
        """
        cur = (start or Path.cwd()).resolve()
        while True:
            candidate = cur / CONFIG_FILENAME
            if candidate.is_file():
                return cls.load(candidate)
            if (cur / ".git").exists():
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        return cls()

    def dump_yaml(self) -> str:
        """Serialize to a YAML string. Suitable for `patchwright init` to write."""
        data: dict[str, Any] = self.model_dump(mode="json")
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)

    # ---------- embargo helpers ----------

    def is_local_url(self, url: str | None) -> bool:
        """True iff `url`'s hostname is in `embargo.local_hosts`."""
        if not url:
            return False
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return host in self.embargo.local_hosts
