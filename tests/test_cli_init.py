"""`patchwright init` CLI command."""

from __future__ import annotations

from pathlib import Path

from patchwright.cli.__main__ import main as cli_main
from patchwright.core.config import CONFIG_FILENAME, PatchwrightConfig


def test_init_writes_default_config(tmp_path: Path) -> None:
    rc = cli_main(["init", "--root", str(tmp_path)])
    assert rc == 0
    target = tmp_path / CONFIG_FILENAME
    assert target.exists()

    # Round trip — what init wrote must parse back to a valid config
    loaded = PatchwrightConfig.load(target)
    assert loaded == PatchwrightConfig()


def test_init_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / CONFIG_FILENAME
    target.write_text("# hand-edited\nllm:\n  effort: low\n", encoding="utf-8")

    rc = cli_main(["init", "--root", str(tmp_path)])
    assert rc == 1
    # Original content preserved
    assert "hand-edited" in target.read_text()


def test_init_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / CONFIG_FILENAME
    target.write_text("# hand-edited\nllm:\n  effort: low\n", encoding="utf-8")

    rc = cli_main(["init", "--root", str(tmp_path), "--force"])
    assert rc == 0

    loaded = PatchwrightConfig.load(target)
    # --force overwrites with defaults
    assert loaded.llm.effort == "high"
    assert "hand-edited" not in target.read_text()


def test_init_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "new" / "project"
    rc = cli_main(["init", "--root", str(nested)])
    assert rc == 0
    assert (nested / CONFIG_FILENAME).exists()


def test_init_output_has_header_comment(tmp_path: Path) -> None:
    rc = cli_main(["init", "--root", str(tmp_path)])
    assert rc == 0
    contents = (tmp_path / CONFIG_FILENAME).read_text()
    assert contents.startswith("#")
    assert "embargo.mode" in contents  # the strict-mode hint we ship in the header
