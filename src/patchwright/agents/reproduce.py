"""reproduce agent — drives TRIAGED → REPRODUCED | NOT_REPRODUCIBLE (AEG-462).

Reads a `poc_spec` artifact off the case, executes it in the injected
`SandboxRunner`, and emits a `repro_log` artifact recording the outcome.
Mirrors `PatchApplyAgent` style: stateless, dataclass-DI, returns bytes
via `AgentResult` — never touches disk.

Routing rules (per the AEG-462 ticket):
  - no `poc_spec` artifact on case        -> NOT_REPRODUCIBLE (no_poc)
  - sandbox exit_code == 0                -> REPRODUCED
  - sandbox exit_code != 0                -> NOT_REPRODUCIBLE (poc_did_not_trigger)
  - sandbox timed_out                     -> NOT_REPRODUCIBLE (timeout)
  - sandbox raises SandboxError           -> NOT_REPRODUCIBLE (sandbox_unavailable)

Why SandboxError -> NOT_REPRODUCIBLE (not REJECTED): the AEG-462 ticket is
explicit. The agent only emits REJECTED when the case data itself is malformed
in a way that can't be salvaged by retrying. Sandbox unavailability is an
operational issue, not a case issue, so we route to NOT_REPRODUCIBLE with a
clear reason and let the operator re-drive the case after fixing infra.

T6 (sandbox escape) is enforced by the SandboxRunner DI — `GVisorSandbox`
(AEG-461) brings runsc + RO FS + no-egress; this agent doesn't re-enforce
those policies, it just calls `runner.run(...)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.config import PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.models import AgentResult, Artifact, Case, Transition
from patchwright.core.sandbox import Mount, SandboxError, SandboxRunner
from patchwright.models.poc import PocSpec
from patchwright.models.repro import ReproLog, ReproVerdict

log = logging.getLogger(__name__)

_STDIO_TAIL_BYTES = 4096


@dataclass
class ReproduceAgent:
    """Stateless agent. Rehydrates from disk every call (CLAUDE.md #3)."""

    sandbox: SandboxRunner
    """Where the PoC actually runs. GVisorSandbox in prod, DockerSandbox in
    dev, a stub in unit tests."""

    case_root: Path
    """Per-case data dir. Scratch for the PoC script lives under
    <case_root>/scratch/<case_id>/poc/."""

    config: PatchwrightConfig = field(default_factory=PatchwrightConfig)
    name: str = "reproduce"
    handles_state: str = field(default=str(State.TRIAGED))

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        spec, spec_artifact_id = _load_poc_spec(case, store)
        if spec is None:
            log.info("reproduce no_poc case=%r", case.id)
            return _not_reproducible(
                case,
                log_bytes=canonical_json(
                    ReproLog(
                        case_id=case.id,
                        verdict="not_reproducible",
                        reason="no PoC attached to case",
                        image=self.config.conventions.repro_image,
                        sandbox_name=self.sandbox.name,
                    ).model_dump(mode="json")
                ),
                reason="no_poc",
            )

        image = spec.image or self.config.conventions.repro_image
        mounts = _materialize_mounts(self.case_root, case.id, spec)

        try:
            run = self.sandbox.run(
                image=image,
                cmd=list(spec.cmd),
                mounts=mounts,
                timeout=spec.timeout_seconds,
            )
        except SandboxError as exc:
            log.info("reproduce sandbox_unavailable case=%r err=%s", case.id, exc)
            return _not_reproducible(
                case,
                log_bytes=canonical_json(
                    ReproLog(
                        case_id=case.id,
                        poc_artifact_id=spec_artifact_id,
                        verdict="not_reproducible",
                        reason=f"sandbox unavailable: {exc}",
                        image=image,
                        cmd=spec.cmd,
                        sandbox_name=self.sandbox.name,
                    ).model_dump(mode="json")
                ),
                reason="sandbox_unavailable",
            )

        if run.timed_out:
            verdict_reason = "PoC timed out"
            transition_reason = "timeout"
        elif run.exit_code == 0:
            verdict_reason = "PoC exited 0; vulnerability reproduced"
            transition_reason = "reproduced"
        else:
            verdict_reason = f"PoC exited {run.exit_code}; vulnerability not triggered"
            transition_reason = "poc_did_not_trigger"

        verdict: ReproVerdict = (
            "reproduced" if (not run.timed_out and run.exit_code == 0) else "not_reproducible"
        )

        log_bytes = canonical_json(
            ReproLog(
                case_id=case.id,
                poc_artifact_id=spec_artifact_id,
                verdict=verdict,
                reason=verdict_reason,
                image=image,
                cmd=spec.cmd,
                exit_code=run.exit_code,
                stdout_tail=run.stdout[-_STDIO_TAIL_BYTES:],
                stderr_tail=run.stderr[-_STDIO_TAIL_BYTES:],
                timed_out=run.timed_out,
                network_enabled=run.network_enabled,
                sandbox_name=self.sandbox.name,
            ).model_dump(mode="json")
        )

        if verdict == "reproduced":
            log.info("reproduce reproduced case=%r", case.id)
            return AgentResult(
                transition=Transition(
                    case_id=case.id,
                    from_state=str(State.TRIAGED),
                    to_state=str(State.REPRODUCED),
                    reason=transition_reason,
                ),
                new_artifacts=[(log_bytes, "repro_log")],
                reason="reproduced",
            )

        log.info(
            "reproduce not_reproducible exit=%d timed_out=%s case=%r",
            run.exit_code,
            run.timed_out,
            case.id,
        )
        return _not_reproducible(
            case,
            log_bytes=log_bytes,
            reason=transition_reason,
        )


# --------------------------------------------------------------------------- helpers


def _load_poc_spec(case: Case, store: ReadOnlyArtifactStore) -> tuple[PocSpec | None, str | None]:
    """Find the latest poc_spec artifact. Returns (None, None) when absent."""
    latest: Artifact | None = None
    for artifact in case.artifacts:
        if artifact.kind == "poc_spec":
            latest = artifact
    if latest is None:
        return None, None
    return PocSpec.model_validate_json(store.get(latest.id)), latest.id


def _materialize_mounts(case_root: Path, case_id: str, spec: PocSpec) -> list[Mount]:
    """When the PocSpec carries a script blob, write it to <case_root>/scratch/<case>/poc/
    and mount that dir read-only at /poc. Otherwise no mounts."""
    if spec.script is None:
        return []
    poc_dir = case_root / "scratch" / case_id / "poc"
    poc_dir.mkdir(parents=True, exist_ok=True)
    (poc_dir / "poc.sh").write_text(spec.script, encoding="utf-8")
    return [Mount(source=poc_dir.resolve(), target="/poc", readonly=True)]


def _not_reproducible(case: Case, *, log_bytes: bytes, reason: str) -> AgentResult:
    return AgentResult(
        transition=Transition(
            case_id=case.id,
            from_state=str(State.TRIAGED),
            to_state=str(State.NOT_REPRODUCIBLE),
            reason=reason,
        ),
        new_artifacts=[(log_bytes, "repro_log")],
        reason=reason,
    )


__all__ = ["ReproduceAgent"]
