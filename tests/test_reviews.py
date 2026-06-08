"""record_human_decision + reviewer_identity tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from patchwright.core.cases import load_case
from patchwright.core.orchestrator import drive, open_case
from patchwright.core.registry import default_registry
from patchwright.core.reviews import (
    UNKNOWN_REVIEWER,
    record_human_decision,
    reviewer_identity,
)


def _build_case(root: Path) -> str:
    case_id = "case-r"
    open_case(case_id=case_id, root=root, raw_report=b'{"id":"R"}')
    drive(case_id, default_registry(), root)
    return case_id


# --------------------------------------------------------------------------- record_human_decision


def test_record_human_decision_appends_entry(tmp_path: Path) -> None:
    case_id = _build_case(tmp_path)
    entry = record_human_decision(
        case_id=case_id,
        root=tmp_path,
        decision="approve",
        reason="LGTM",
        identity="olivia@example.com",
    )
    assert entry.kind == "human_decision"
    assert entry.author == "human:olivia@example.com"
    assert entry.payload["decision"] == "approve"
    assert entry.payload["reason"] == "LGTM"
    assert entry.payload["case_state_at_review"] == "REJECTED"

    # Verify it's persisted in the case journal
    record = load_case(case_id, tmp_path)
    assert any(e.kind == "human_decision" for e in record.entries)


def test_record_human_decision_does_not_transition_fsm(tmp_path: Path) -> None:
    case_id = _build_case(tmp_path)
    state_before = load_case(case_id, tmp_path).case.state
    record_human_decision(
        case_id=case_id, root=tmp_path, decision="approve", reason="", identity="o@e"
    )
    state_after = load_case(case_id, tmp_path).case.state
    assert state_before == state_after  # human_decision is informational only


def test_record_human_decision_invalid_verb_raises(tmp_path: Path) -> None:
    case_id = _build_case(tmp_path)
    with pytest.raises(ValueError, match="unknown decision"):
        record_human_decision(
            case_id=case_id,
            root=tmp_path,
            decision="nuke",  # type: ignore[arg-type]
            reason="",
            identity="o@e",
        )


def test_record_human_decision_missing_case_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        record_human_decision(
            case_id="case-ghost",
            root=tmp_path,
            decision="approve",
            reason="",
            identity="o@e",
        )


# --------------------------------------------------------------------------- reviewer_identity


def test_reviewer_identity_override_wins() -> None:
    assert reviewer_identity(override="alice@example.com") == "alice@example.com"


def test_reviewer_identity_strips_whitespace() -> None:
    assert reviewer_identity(override="  bob@example.com  ") == "bob@example.com"


def test_reviewer_identity_falls_back_to_git_config() -> None:
    """When no override, use git config user.email if available."""
    with (
        patch("shutil.which", return_value="/usr/bin/git"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "carol@example.com\n"
        assert reviewer_identity() == "carol@example.com"


def test_reviewer_identity_falls_back_to_getlogin_when_no_git() -> None:
    with (
        patch("shutil.which", return_value=None),
        patch("os.getlogin", return_value="dave"),
    ):
        assert reviewer_identity() == "dave"


def test_reviewer_identity_unknown_when_getlogin_fails() -> None:
    with (
        patch("shutil.which", return_value=None),
        patch("os.getlogin", side_effect=OSError("no controlling terminal")),
    ):
        assert reviewer_identity() == UNKNOWN_REVIEWER


def test_reviewer_identity_unknown_when_git_returns_empty() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/git"),
        patch("subprocess.run") as mock_run,
        patch("os.getlogin", side_effect=OSError("nope")),
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        assert reviewer_identity() == UNKNOWN_REVIEWER
