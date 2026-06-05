"""Evidence packet renderer — sections present, content rendered."""

from __future__ import annotations

from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.cases import load_case
from patchwright.core.evidence import render
from patchwright.core.orchestrator import case_root_paths, drive, open_case
from patchwright.core.registry import default_registry


def _build_case(root: Path, case_id: str = "case-ev") -> tuple[Path, str]:
    open_case(case_id=case_id, root=root, raw_report=b'{"id":"R1","summary":"NULL deref"}')
    drive(case_id, default_registry(), root)
    paths = case_root_paths(root, case_id)
    return paths["artifacts_dir"], case_id


def test_render_includes_all_sections(tmp_path: Path) -> None:
    artifacts_dir, case_id = _build_case(tmp_path)
    record = load_case(case_id, tmp_path)
    store = ArtifactStore(artifacts_dir).read_only()

    md = render(record.case, record.entries, store)

    assert f"# Case `{case_id}`" in md
    assert "## Origin" in md
    assert "## Timeline" in md
    assert "## Artifacts" in md
    assert "## Reasoning trace" in md


def test_render_shows_state_and_artifact_count(tmp_path: Path) -> None:
    artifacts_dir, case_id = _build_case(tmp_path)
    record = load_case(case_id, tmp_path)
    store = ArtifactStore(artifacts_dir).read_only()

    md = render(record.case, record.entries, store)
    assert "**State:**" in md
    assert "DONE" in md  # noop_closer takes it to DONE
    assert "Artifacts attached" in md


def test_render_embeds_raw_report_content(tmp_path: Path) -> None:
    artifacts_dir, case_id = _build_case(tmp_path)
    record = load_case(case_id, tmp_path)
    store = ArtifactStore(artifacts_dir).read_only()

    md = render(record.case, record.entries, store)
    assert "NULL deref" in md  # the report body should be excerpted under Origin


def test_render_timeline_has_one_row_per_entry(tmp_path: Path) -> None:
    artifacts_dir, case_id = _build_case(tmp_path)
    record = load_case(case_id, tmp_path)
    store = ArtifactStore(artifacts_dir).read_only()

    md = render(record.case, record.entries, store)
    timeline_section = md.split("## Timeline", 1)[1].split("##", 1)[0]
    # Row lines start with "| " (header + data); the separator starts with "|-".
    row_count = sum(1 for line in timeline_section.splitlines() if line.startswith("| "))
    # 1 header row + N entry rows
    assert row_count == len(record.entries) + 1


def test_render_handles_case_without_raw_report(tmp_path: Path) -> None:
    """Defensive: if Case.artifacts has no raw_report, render still works."""
    from patchwright.core.models import Case  # noqa: PLC0415 - test-local

    case = Case(
        id="case-empty",
        state="INTAKE",
        created_at="2026-06-04T00:00:00.000000Z",
        artifacts=[],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    store = ArtifactStore(tmp_path / "art").read_only()
    md = render(case, [], store)
    assert "no raw_report artifact" in md
    assert "_(no entries)_" in md
