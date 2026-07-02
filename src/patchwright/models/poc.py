"""PocSpec — structured proof-of-concept the reproduce agent runs (AEG-462).

Attached to a case as a `kind="poc_spec"` artifact. The simplest contract: an
OCI image + command line, optionally with an attached shell script blob the
agent mounts read-only into the sandbox before running the cmd.

Where does a PocSpec come from in P1?
  - Operator drops one into the case before driving the FSM
  - A future intake adapter (e.g. VINCE) might emit one directly
  - LLM-derived PoCs (FR-RP-3, P2) eventually populate this artifact via a
    dedicated agent

The reproduce agent never builds a PocSpec — it only consumes one. When no
poc_spec artifact exists on the case, the agent transitions
TRIAGED → NOT_REPRODUCIBLE with a structured reason. That's the
"no PoC provided" path required by AEG-462.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PocSpec(BaseModel):
    """One PoC the reproduce agent can execute in a SandboxRunner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"

    image: str | None = Field(
        default=None,
        description=(
            "Container image to run the PoC in. None falls back to "
            "`config.conventions.repro_image`. Pinning here lets a PoC carry "
            "its own dependency surface (specific Python / Node / etc.)."
        ),
    )

    cmd: tuple[str, ...] = Field(
        min_length=1,
        description="argv to execute inside the sandbox. Mandatory.",
    )

    script: str | None = Field(
        default=None,
        description=(
            "Optional shell script. When set, the reproduce agent writes it to "
            "the scratch dir as `poc.sh` and mounts it read-only at /poc. "
            "Typical `cmd` then references it as ['sh', '/poc/poc.sh']."
        ),
    )

    timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=600.0,
        description=(
            "Hard timeout for the sandboxed run. Capped at 10 minutes so a "
            "wedged or adversarial PoC can't stall the pipeline."
        ),
    )
