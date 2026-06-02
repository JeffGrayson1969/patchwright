from __future__ import annotations

from typing import Protocol, runtime_checkable

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.models import AgentResult, Case


@runtime_checkable
class Agent(Protocol):
    """Stateless agent protocol.

    An Agent is a callable that takes the current Case + a read-only artifact
    store and returns an AgentResult (proposed transition + evidence bytes).
    The orchestrator owns all disk I/O and the journal append.
    """

    name: str
    handles_state: str

    def __call__(
        self,
        case: Case,
        store: ReadOnlyArtifactStore,
    ) -> AgentResult: ...
