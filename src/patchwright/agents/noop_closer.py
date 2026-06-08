# noop_closer — P0 hello-world demo agent. Rejects a TRIAGED case to reach a
# terminal state. TRIAGED->DONE was removed (CLAUDE.md #8: no shortcut past review).
from __future__ import annotations

from dataclasses import dataclass

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.fsm import State
from patchwright.core.models import AgentResult, Case, Transition


@dataclass
class _NoopCloser:
    name: str = "noop_closer"
    handles_state: str = str(State.TRIAGED)

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        del store
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.TRIAGED),
                to_state=str(State.REJECTED),
                reason="noop: reject for hello-world demo (no real repro agent)",
            ),
            new_artifacts=[],
            reason="noop reject",
        )


agent: _NoopCloser = _NoopCloser()
