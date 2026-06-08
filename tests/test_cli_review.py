"""CLI tests for list / explain / review.

`review` invokes $EDITOR — we patch `_invoke_editor` so the test runs an
in-process callback that simulates the operator's edit instead of spawning a
real editor process.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from patchwright.cli.__main__ import main as cli_main
from patchwright.cli.review import _parse_decision
from patchwright.core.cases import load_case
from patchwright.core.orchestrator import drive, open_case
from patchwright.core.registry import default_registry


def _build_case(root: Path, case_id: str = "case-cli") -> str:
    open_case(case_id=case_id, root=root, raw_report=b'{"id":"R","summary":"x"}')
    drive(case_id, default_registry(), root)
    return case_id


# --------------------------------------------------------------------------- list


def test_list_empty_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["list", "--root", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "no cases found" in captured.err


def test_list_shows_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _build_case(tmp_path, "case-a")
    _build_case(tmp_path, "case-b")
    rc = cli_main(["list", "--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "case-a" in out
    assert "case-b" in out
    assert "REJECTED" in out  # noop_closer now emits TRIAGED->REJECTED
    assert "CASE" in out  # header


def test_list_filters_by_state(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _build_case(tmp_path, "case-done")
    rc = cli_main(["list", "--root", str(tmp_path), "--state", "INTAKE"])
    assert rc == 0
    out = capsys.readouterr().err
    assert "no cases found" in out  # filter excludes the REJECTED case


# --------------------------------------------------------------------------- explain


def test_explain_prints_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case_id = _build_case(tmp_path)
    rc = cli_main(["explain", case_id, "--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"# Case `{case_id}`" in out
    assert "## Timeline" in out


def test_explain_missing_case_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_main(["explain", "case-ghost", "--root", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


# --------------------------------------------------------------------------- _parse_decision (unit)


def test_parse_decision_returns_none_for_placeholder() -> None:
    content = "header\n## Decision\nTYPE_DECISION_HERE\n## Reason\nbecause\n"
    assert _parse_decision(content) == (None, "")


def test_parse_decision_handles_each_verb() -> None:
    for verb in ("approve", "edit", "reject", "fork"):
        content = f"## Decision\n{verb}\n## Reason\nbecause\n"
        result = _parse_decision(content)
        assert result == (verb, "because")


def test_parse_decision_strips_comments_and_blanks() -> None:
    content = (
        "## Decision\n\n<!-- comment -->\n   approve   \n## Reason\n\n<!-- pick -->\n  LGTM  \n"
    )
    assert _parse_decision(content) == ("approve", "LGTM")


def test_parse_decision_rejects_unknown_verb() -> None:
    content = "## Decision\nNUKE\n## Reason\nwhy\n"
    assert _parse_decision(content) == (None, "")


def test_parse_decision_missing_section_returns_none() -> None:
    content = "no decision section here"
    assert _parse_decision(content) == (None, "")


# --------------------------------------------------------------------------- review (full CLI)


def _make_editor_writing(verb: str, reason: str = "tested") -> Callable[[Path], None]:
    """Return a fake _invoke_editor that overwrites the temp file with a
    finished decision packet."""

    def _editor(path: Path) -> None:
        path.write_text(
            f"# placeholder\n\n## Decision\n{verb}\n\n## Reason\n{reason}\n",
            encoding="utf-8",
        )

    return _editor


def test_review_approve_records_human_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case_id = _build_case(tmp_path)
    with patch(
        "patchwright.cli.review._invoke_editor", side_effect=_make_editor_writing("approve", "LGTM")
    ):
        rc = cli_main(["review", case_id, "--root", str(tmp_path), "--as", "olivia@example.com"])
    assert rc == 0

    record = load_case(case_id, tmp_path)
    decisions = [e for e in record.entries if e.kind == "human_decision"]
    assert len(decisions) == 1
    assert decisions[0].author == "human:olivia@example.com"
    assert decisions[0].payload["decision"] == "approve"
    assert decisions[0].payload["reason"] == "LGTM"

    err = capsys.readouterr().err
    assert "recorded 'approve'" in err
    assert "olivia@example.com" in err


def test_review_reject_records_human_decision(tmp_path: Path) -> None:
    case_id = _build_case(tmp_path)
    with patch(
        "patchwright.cli.review._invoke_editor",
        side_effect=_make_editor_writing("reject", "false positive"),
    ):
        rc = cli_main(["review", case_id, "--root", str(tmp_path), "--as", "rev@e"])
    assert rc == 0

    record = load_case(case_id, tmp_path)
    decision = next(e for e in record.entries if e.kind == "human_decision")
    assert decision.payload["decision"] == "reject"
    assert decision.payload["reason"] == "false positive"


def test_review_placeholder_aborts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """If the operator leaves TYPE_DECISION_HERE in place, abort without
    recording (return code 2)."""
    case_id = _build_case(tmp_path)

    def noop_editor(path: Path) -> None:
        del path  # leave the file with the original template untouched

    with patch("patchwright.cli.review._invoke_editor", side_effect=noop_editor):
        rc = cli_main(["review", case_id, "--root", str(tmp_path), "--as", "o@e"])
    assert rc == 2

    record = load_case(case_id, tmp_path)
    assert not any(e.kind == "human_decision" for e in record.entries)

    err = capsys.readouterr().err
    assert "aborted" in err


def test_review_missing_case_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_main(["review", "case-ghost", "--root", str(tmp_path), "--as", "o@e"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_review_uses_git_identity_when_no_override(tmp_path: Path) -> None:
    case_id = _build_case(tmp_path)
    with (
        patch(
            "patchwright.cli.review._invoke_editor",
            side_effect=_make_editor_writing("approve", "ok"),
        ),
        # Patch the name as imported into cli.review (not the source module),
        # since cli.review did `from ... import reviewer_identity`.
        patch(
            "patchwright.cli.review.reviewer_identity",
            return_value="git-config-user@example.com",
        ),
    ):
        rc = cli_main(["review", case_id, "--root", str(tmp_path)])
    assert rc == 0

    record = load_case(case_id, tmp_path)
    decision = next(e for e in record.entries if e.kind == "human_decision")
    assert decision.author == "human:git-config-user@example.com"
