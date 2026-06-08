"""Test #6 (added per plan open question) — enumerate FSM edges.

Catches accidental graph edits in P1+. Cheap to maintain: O(edges).
"""

from __future__ import annotations

import pytest

from patchwright.core.fsm import INITIAL_STATE, TERMINAL_STATES, State, is_legal, legal_targets

LEGAL_EDGES: list[tuple[State, State]] = [
    (State.INTAKE, State.TRIAGED),
    (State.INTAKE, State.REJECTED),
    (State.TRIAGED, State.REPRODUCED),
    (State.TRIAGED, State.DONE),
    (State.TRIAGED, State.REJECTED),
    (State.REPRODUCED, State.PATCH_PROPOSED),
    (State.REPRODUCED, State.REJECTED),
    (State.PATCH_PROPOSED, State.DONE),
    (State.PATCH_PROPOSED, State.REJECTED),
]


@pytest.mark.parametrize("frm,to", LEGAL_EDGES)
def test_each_documented_edge_is_legal(frm: State, to: State) -> None:
    assert is_legal(str(frm), str(to))


def test_terminal_states_have_no_outgoing_edges() -> None:
    for terminal in TERMINAL_STATES:
        assert legal_targets(str(terminal)) == frozenset()


def test_initial_state_is_intake() -> None:
    assert INITIAL_STATE is State.INTAKE


def test_unknown_states_are_illegal() -> None:
    assert not is_legal("INTAKE", "NOT_A_REAL_STATE")
    assert not is_legal("NOPE", "DONE")
    assert legal_targets("NOPE") == frozenset()


def test_no_self_loops() -> None:
    for s in State:
        assert s not in legal_targets(str(s))


def test_complete_edge_coverage() -> None:
    """The documented LEGAL_EDGES list MUST cover the FSM exactly. If a P1+
    change adds or removes an edge, this test forces the list to be updated."""
    actual: set[tuple[State, State]] = set()
    for s in State:
        for t in legal_targets(str(s)):
            actual.add((s, t))
    assert actual == set(LEGAL_EDGES)
