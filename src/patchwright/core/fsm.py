from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    INTAKE = "INTAKE"
    TRIAGED = "TRIAGED"
    REJECTED = "REJECTED"
    NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"
    REPRODUCED = "REPRODUCED"
    PATCH_PROPOSED = "PATCH_PROPOSED"
    PATCH_APPLIED = "PATCH_APPLIED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    DONE = "DONE"


INITIAL_STATE: State = State.INTAKE

TERMINAL_STATES: frozenset[State] = frozenset({State.DONE, State.REJECTED, State.NOT_REPRODUCIBLE})

# Full Wave B graph per phase1-work-plan.md. Shortcut edges (TRIAGED->DONE,
# PATCH_PROPOSED->DONE) removed — human review is the only path to DONE (CLAUDE.md #8).
_GRAPH: dict[State, frozenset[State]] = {
    State.INTAKE: frozenset({State.TRIAGED, State.REJECTED}),
    State.TRIAGED: frozenset({State.REPRODUCED, State.NOT_REPRODUCIBLE, State.REJECTED}),
    State.REPRODUCED: frozenset({State.PATCH_PROPOSED, State.REJECTED}),
    State.PATCH_PROPOSED: frozenset({State.PATCH_APPLIED, State.REJECTED}),  # cross_checker drives this edge (M2.5 / T9)
    State.PATCH_APPLIED: frozenset({State.AWAITING_REVIEW}),
    State.AWAITING_REVIEW: frozenset({State.DONE, State.REJECTED}),
    State.NOT_REPRODUCIBLE: frozenset(),
    State.REJECTED: frozenset(),
    State.DONE: frozenset(),
}


def is_legal(from_state: str, to_state: str) -> bool:
    """True iff (from_state, to_state) is an edge in the FSM."""
    try:
        f = State(from_state)
        t = State(to_state)
    except ValueError:
        return False
    return t in _GRAPH[f]


def legal_targets(from_state: str) -> frozenset[State]:
    """All states reachable from from_state in one step."""
    try:
        f = State(from_state)
    except ValueError:
        return frozenset()
    return _GRAPH[f]


def is_terminal(state: str) -> bool:
    try:
        return State(state) in TERMINAL_STATES
    except ValueError:
        return False
