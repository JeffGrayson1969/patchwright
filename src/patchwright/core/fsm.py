from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    INTAKE = "INTAKE"
    TRIAGED = "TRIAGED"
    REJECTED = "REJECTED"
    DONE = "DONE"


INITIAL_STATE: State = State.INTAKE

TERMINAL_STATES: frozenset[State] = frozenset({State.DONE, State.REJECTED})

# P0 graph: minimal but exercises branching (TRIAGED can fork to DONE or REJECTED).
_GRAPH: dict[State, frozenset[State]] = {
    State.INTAKE: frozenset({State.TRIAGED, State.REJECTED}),
    State.TRIAGED: frozenset({State.DONE, State.REJECTED}),
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
