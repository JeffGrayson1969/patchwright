"""Test #4 — orchestrator rejects illegal transitions.

Invariant: if an agent proposes a transition that's not in the FSM, drive()
raises IllegalTransition, the journal records the rejection, and the case
state is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.errors import IllegalTransition
from patchwright.core.fsm import State
from patchwright.core.journal import Journal
from patchwright.core.models import AgentResult, Case, Transition
from patchwright.core.orchestrator import case_root_paths, drive, open_case
from patchwright.core.registry import Registry


@dataclass(frozen=True)
class _BadAgent:
    name: str = "bad_agent"
    handles_state: str = str(State.INTAKE)

    def __call__(self, case: Case, _store: ReadOnlyArtifactStore) -> AgentResult:
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.INTAKE),
                to_state=str(State.DONE),  # illegal — INTAKE -> DONE is not an edge
                reason="bad",
            ),
            new_artifacts=[],
            reason="bad",
        )


def test_illegal_transition_raises_and_journals_rejection(tmp_path: Path) -> None:
    case_id = "case-illegal-test"
    open_case(case_id=case_id, root=tmp_path, raw_report=b"{}")

    registry = Registry()
    registry.register(_BadAgent())

    with pytest.raises(IllegalTransition):
        drive(case_id, registry, tmp_path)

    j = Journal(case_root_paths(tmp_path, case_id)["journal_dir"])
    entries = j.read()
    assert entries[-1].kind == "agent_rejected"
    assert "illegal transition" in entries[-1].payload["reason"]

    # No transition was applied: state still INTAKE.
    state_kinds = [e.kind for e in entries]
    assert "transition" not in state_kinds
