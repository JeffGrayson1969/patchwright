"""noop_triage — the P0 trivial triage agent.

Transitions INTAKE -> TRIAGED and emits a stub triage_packet artifact.
Demonstrates the agent contract: stateless, pure-ish over (Case, ReadOnlyStore),
returns AgentResult with bytes for any new artifacts. Never touches disk.
"""

from __future__ import annotations

from dataclasses import dataclass

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.models import AgentResult, Case, Transition


@dataclass
class _NoopTriage:
    name: str = "noop_triage"
    handles_state: str = str(State.INTAKE)

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        del store  # P0 noop_triage emits a fixed payload — no artifact read needed.
        triage_packet = {
            "case_id": case.id,
            "summary": "noop triage — fixed payload for P0 hello-world demo",
            "confidence": 1.0,
            "dedup_result": "no_match",
            "reporter_trust": 0.0,
            "decision": "advance",
        }
        # canonical_json so the artifact's sha is reproducible across runs.
        packet_bytes = canonical_json(triage_packet)
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.INTAKE),
                to_state=str(State.TRIAGED),
                reason="noop: advance for hello-world",
            ),
            new_artifacts=[(packet_bytes, "triage_packet")],
            reason="noop triage",
        )


agent: _NoopTriage = _NoopTriage()
