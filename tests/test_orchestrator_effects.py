"""orchestrator.drive() effect-runner wiring (AEG-425).

Verifies the new TransitionEffects registry + drive() integration:
  - effects fire exactly once after a journaled transition
  - effects are gated on (from_state, to_state) — wrong-edge effects skip
  - drive() with no effects is byte-identical to P0 behavior
  - an effect that raises is caught by run(); drive() does not crash
  - journal hash chain stays intact across effect appends
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from patchwright.core.artifacts import ArtifactStore, ReadOnlyArtifactStore
from patchwright.core.config import PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.journal import Journal
from patchwright.core.models import AgentResult, Artifact, Case, Transition
from patchwright.core.orchestrator import (
    EffectContext,
    TransitionEffects,
    case_root_paths,
    drive,
    open_case,
    replay,
)
from patchwright.core.registry import Registry

# --------------------------------------------------------------------------- fixture agents


class _StubAgent:
    """A canned agent that drives a single transition deterministically."""

    def __init__(
        self,
        *,
        name: str,
        handles_state: str,
        to_state: str,
        artifacts: list[tuple[bytes, str]] | None = None,
    ) -> None:
        self.name = name
        self.handles_state = handles_state
        self._to_state = to_state
        self._artifacts = artifacts or []

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=case.state,
                to_state=self._to_state,
                reason=f"{self.name} -> {self._to_state}",
            ),
            new_artifacts=self._artifacts,
            reason="ok",
        )


# --------------------------------------------------------------------------- helpers


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "pw_root"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _open_intake_case(root: Path) -> Case:
    return open_case(case_id="case-effects-test", root=root, raw_report=b"{}")


def _registry_intake_to_triaged() -> Registry:
    r = Registry()
    r.register(
        _StubAgent(
            name="stub_triage",
            handles_state=str(State.INTAKE),
            to_state=str(State.TRIAGED),
        ),
    )
    return r


def _journal_entries(root: Path, case_id: str) -> list[dict[str, Any]]:
    paths = case_root_paths(root, case_id)
    j = Journal(paths["journal_dir"])
    return [e.model_dump() for e in j.read()]


# --------------------------------------------------------------------------- effect fires once


def test_effect_fires_for_matching_transition_key(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _open_intake_case(root)
    registry = _registry_intake_to_triaged()

    calls: list[EffectContext] = []
    effects = TransitionEffects()
    effects.register(
        (str(State.INTAKE), str(State.TRIAGED)),
        calls.append,
    )

    drive(
        "case-effects-test",
        registry,
        root,
        config=PatchwrightConfig(),
        effects=effects,
        workspace_root=tmp_path,
    )

    assert len(calls) == 1
    ctx = calls[0]
    assert ctx.case.id == "case-effects-test"
    assert ctx.case.state == str(State.TRIAGED)


def test_effect_does_not_fire_for_other_transition_keys(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _open_intake_case(root)
    registry = _registry_intake_to_triaged()

    calls: list[EffectContext] = []
    effects = TransitionEffects()
    # Register against a transition that won't happen in this drive
    effects.register(
        (str(State.PATCH_APPLIED), str(State.AWAITING_REVIEW)),
        calls.append,
    )

    drive(
        "case-effects-test",
        registry,
        root,
        config=PatchwrightConfig(),
        effects=effects,
        workspace_root=tmp_path,
    )

    assert calls == []


# --------------------------------------------------------------------------- back-compat


def test_drive_without_effects_kwargs_is_p0_behavior(tmp_path: Path) -> None:
    """drive() with no config / effects / workspace_root must behave exactly
    as it did in P0 — no effect lookup, no extra journal entries."""
    root = _make_root(tmp_path)
    _open_intake_case(root)
    registry = _registry_intake_to_triaged()

    drive("case-effects-test", registry, root)

    entries = _journal_entries(root, "case-effects-test")
    kinds = [e["kind"] for e in entries]
    # Expected: case_opened, transition. NO artifact_written from effects.
    assert kinds == ["case_opened", "transition"]


def test_drive_with_partial_effects_kwargs_does_not_fire(tmp_path: Path) -> None:
    """Effects only fire when ALL three optional kwargs are supplied. Missing
    workspace_root or config keeps the engine in P0 mode (back-compat)."""
    root = _make_root(tmp_path)
    _open_intake_case(root)
    registry = _registry_intake_to_triaged()

    calls: list[EffectContext] = []
    effects = TransitionEffects()
    effects.register((str(State.INTAKE), str(State.TRIAGED)), calls.append)

    # No config + no workspace_root -> effects skipped even though registered
    drive("case-effects-test", registry, root, effects=effects)

    assert calls == []


# --------------------------------------------------------------------------- raise -> caught


def test_effect_that_raises_does_not_crash_drive(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _open_intake_case(root)
    registry = _registry_intake_to_triaged()

    def boom(ctx: EffectContext) -> None:
        raise RuntimeError("synthetic")

    effects = TransitionEffects()
    effects.register((str(State.INTAKE), str(State.TRIAGED)), boom)

    final = drive(
        "case-effects-test",
        registry,
        root,
        config=PatchwrightConfig(),
        effects=effects,
        workspace_root=tmp_path,
    )

    # Transition completed successfully despite the effect crashing
    assert final.state == str(State.TRIAGED)


# --------------------------------------------------------------------------- hash chain integrity


def test_effect_appended_entries_extend_hash_chain(tmp_path: Path) -> None:
    """An effect that journals must extend the chain — never break it.
    Replay-after-effect picks up the new last_hash so subsequent agents use
    the correct prev_hash."""
    root = _make_root(tmp_path)
    _open_intake_case(root)
    registry = _registry_intake_to_triaged()

    def journal_one(ctx: EffectContext) -> None:
        ctx.journal.append(
            case_id=ctx.case.id,
            kind="artifact_written",
            author="system:test_effect",
            payload={"kind": "test_marker", "info": "synthetic"},
            prev_hash=ctx.case.last_hash,
            seq=ctx.case.last_seq + 1,
        )

    effects = TransitionEffects()
    effects.register((str(State.INTAKE), str(State.TRIAGED)), journal_one)

    drive(
        "case-effects-test",
        registry,
        root,
        config=PatchwrightConfig(),
        effects=effects,
        workspace_root=tmp_path,
    )

    paths = case_root_paths(root, "case-effects-test")
    entries = Journal(paths["journal_dir"]).read()  # raises if chain broken
    assert [e.kind for e in entries] == [
        "case_opened",
        "transition",
        "artifact_written",
    ]
    assert entries[-1].payload["kind"] == "test_marker"

    # Final replay reflects the effect's journal append (last_seq advanced)
    final = replay(Journal(paths["journal_dir"]), ArtifactStore(paths["artifacts_dir"]))
    assert final is not None
    assert final.last_seq == 2


# --------------------------------------------------------------------------- TransitionEffects unit


def test_transition_effects_multiple_registrations_run_in_order() -> None:
    effects = TransitionEffects()
    calls: list[str] = []

    def a(_ctx: EffectContext) -> None:
        calls.append("a")

    def b(_ctx: EffectContext) -> None:
        calls.append("b")

    key = (str(State.INTAKE), str(State.TRIAGED))
    effects.register(key, a)
    effects.register(key, b)

    # Build a synthetic context (we don't need a real journal for this unit test)
    case = Case(
        id="c",
        state=str(State.TRIAGED),
        created_at="2026-06-18T00:00:00.000000Z",
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    ctx = EffectContext(
        case=case,
        store=ArtifactStore(Path("/tmp")),
        journal=Journal(Path("/tmp")),
        config=PatchwrightConfig(),
        workspace_root=Path("/tmp"),
    )
    effects.run(ctx, from_state=str(State.INTAKE))
    assert calls == ["a", "b"]


def test_transition_effects_registered_for_returns_immutable_view() -> None:
    effects = TransitionEffects()
    key = (str(State.INTAKE), str(State.TRIAGED))

    def fn(_ctx: EffectContext) -> None:  # pragma: no cover
        pass

    effects.register(key, fn)
    registered = effects.registered_for(key)
    assert registered == (fn,)
    assert isinstance(registered, tuple)


# ------------------------------------------------------------ transition artifacts still attach


def test_agent_emitted_artifacts_still_attach_when_effects_armed(tmp_path: Path) -> None:
    """Sanity: arming effects must not break the existing artifact-attach path
    from transition payloads."""
    root = _make_root(tmp_path)
    _open_intake_case(root)

    artifact_bytes = canonical_json({"hello": "world"})
    registry = Registry()
    registry.register(
        _StubAgent(
            name="stub_triage",
            handles_state=str(State.INTAKE),
            to_state=str(State.TRIAGED),
            artifacts=[(artifact_bytes, "test_packet")],
        ),
    )

    effects = TransitionEffects()
    drive(
        "case-effects-test",
        registry,
        root,
        config=PatchwrightConfig(),
        effects=effects,
        workspace_root=tmp_path,
    )

    paths = case_root_paths(root, "case-effects-test")
    final = replay(Journal(paths["journal_dir"]), ArtifactStore(paths["artifacts_dir"]))
    assert final is not None
    kinds = [a.kind for a in final.artifacts]
    assert "test_packet" in kinds


# ----------------------------------------------------------- Artifact import keeps mypy quiet


_ = Artifact  # re-exported for editor / mypy linkage
