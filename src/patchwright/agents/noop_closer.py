"""noop_closer — closes a TRIAGED case to DONE for the P0 hello-world demo.

Two agents (not one) demonstrates the registry+FSM dispatch generalizes
beyond a single transition.
"""

from __future__ import annotations

from dataclasses import dataclass

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.fsm import State
from patchwright.core.models import AgentResult, Case, Transition


@dataclass
class _NoopCloser:
    name: str = "noop_closer"
    handles_state: str = str(State.TRIAGED)

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:  # noqa: ARG002
        del store
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.TRIAGED),
                to_state=str(State.DONE),
                reason="noop: close for hello-world",
            ),
            new_artifacts=[],
            reason="noop close",
        )


agent: _NoopCloser = _NoopCloser()
