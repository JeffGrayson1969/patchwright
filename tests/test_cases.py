"""Case enumeration + lookup helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from patchwright.core.cases import list_all_cases, list_case_ids, load_case
from patchwright.core.orchestrator import drive, open_case
from patchwright.core.registry import default_registry


def _make_case(root: Path, case_id: str, report: bytes = b'{"id":"R"}') -> None:
    open_case(case_id=case_id, root=root, raw_report=report)
    drive(case_id, default_registry(), root)


def test_list_case_ids_empty_root(tmp_path: Path) -> None:
    assert list_case_ids(tmp_path) == []


def test_list_case_ids_returns_sorted(tmp_path: Path) -> None:
    _make_case(tmp_path, "case-b")
    _make_case(tmp_path, "case-a")
    _make_case(tmp_path, "case-c")
    assert list_case_ids(tmp_path) == ["case-a", "case-b", "case-c"]


def test_load_case_replays_state(tmp_path: Path) -> None:
    _make_case(tmp_path, "case-1")
    record = load_case("case-1", tmp_path)
    assert record.case.id == "case-1"
    # noop_closer emits TRIAGED->REJECTED; TRIAGED->DONE was a shortcut removed in review #3.
    assert record.case.state == "REJECTED"
    assert len(record.entries) >= 1
    assert record.entries[0].kind == "case_opened"


def test_load_case_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_case("case-does-not-exist", tmp_path)


def test_list_all_cases_returns_records(tmp_path: Path) -> None:
    _make_case(tmp_path, "case-a")
    _make_case(tmp_path, "case-b")
    records = list_all_cases(tmp_path)
    assert {r.case.id for r in records} == {"case-a", "case-b"}


def test_list_all_cases_skips_empty_directories(tmp_path: Path) -> None:
    _make_case(tmp_path, "real-case")
    # Empty directory shouldn't crash list_all_cases.
    (tmp_path / "journal" / "junk-dir").mkdir()
    records = list_all_cases(tmp_path)
    assert [r.case.id for r in records] == ["real-case"]
