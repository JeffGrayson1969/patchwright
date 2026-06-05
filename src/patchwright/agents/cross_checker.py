"""cross_checker — stub for M2.5 (T9 mitigation).

The cross-checker re-evaluates a primary agent's proposed transition using
a *different* LLMProvider. It refuses the transition if it cannot reconstruct
the same intent from the original report.

Full implementation lands in M2.5 (Wave B). This stub exists so:
  - the registry has the agent slot
  - the entry-point group has a default member
  - downstream code can import the symbol

When wired in M2.5, the cross-checker will sit between PATCH_PROPOSED and
PATCH_APPLIED. In P1 it does NOT participate in the INTAKE → TRIAGED path
(triage decisions are reversible by the human reviewer in M4).
"""

from __future__ import annotations

from dataclasses import dataclass

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.llm import LLMProvider
from patchwright.core.models import AgentResult, Case


@dataclass
class CrossCheckerAgent:
    """Stub. Wired up in M2.5."""

    provider: LLMProvider
    name: str = "cross_checker"
    handles_state: str = ""  # Not registered to any state until M2.5.

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        del store
        raise NotImplementedError(f"cross_checker is a stub; M2.5 wires it in for case {case.id!r}")
