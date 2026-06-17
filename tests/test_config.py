"""PatchwrightConfig — schema validation, load/discover, YAML round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from patchwright.core.config import (
    CONFIG_FILENAME,
    ConfigError,
    PatchwrightConfig,
)


def test_all_defaults_validate() -> None:
    config = PatchwrightConfig()
    assert config.llm.provider == "anthropic"
    assert config.llm.effort == "high"
    assert config.embargo.default_days == 90
    assert config.embargo.critical_days == 14
    assert config.embargo.mode == "normal"
    assert config.sandbox.backend == "docker"
    assert config.conventions.test_command == "pytest"


def test_extra_top_level_field_rejected() -> None:
    from pydantic import ValidationError  # noqa: PLC0415 - test-local import

    with pytest.raises(ValidationError):
        PatchwrightConfig.model_validate({"unknown_section": {}})


def test_load_empty_file_yields_defaults(tmp_path: Path) -> None:
    target = tmp_path / "patchwright.yaml"
    target.write_text("", encoding="utf-8")
    config = PatchwrightConfig.load(target)
    assert config == PatchwrightConfig()


def test_load_partial_overrides_only_named_sections(tmp_path: Path) -> None:
    target = tmp_path / "patchwright.yaml"
    target.write_text(
        "llm:\n  effort: low\n  model: claude-haiku-4-5\n",
        encoding="utf-8",
    )
    config = PatchwrightConfig.load(target)
    assert config.llm.effort == "low"
    assert config.llm.model == "claude-haiku-4-5"
    # Other sections still defaulted.
    assert config.embargo.default_days == 90
    assert config.sandbox.backend == "docker"


def test_load_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    target = tmp_path / "patchwright.yaml"
    target.write_text("llm:\n  effort: [not valid yaml here :::", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML parse"):
        PatchwrightConfig.load(target)


def test_load_unknown_enum_value_raises_config_error(tmp_path: Path) -> None:
    target = tmp_path / "patchwright.yaml"
    target.write_text("llm:\n  effort: galaxy_brain\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid config"):
        PatchwrightConfig.load(target)


def test_load_missing_file_raises_config_error(tmp_path: Path) -> None:
    target = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigError, match="not found"):
        PatchwrightConfig.load(target)


def test_load_non_mapping_root_rejected(tmp_path: Path) -> None:
    target = tmp_path / "patchwright.yaml"
    target.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a mapping"):
        PatchwrightConfig.load(target)


# --------------------------------------------------------------------------- discover


def test_discover_finds_config_in_cwd(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("llm:\n  effort: medium\n", encoding="utf-8")
    config = PatchwrightConfig.discover(start=tmp_path)
    assert config.llm.effort == "medium"


def test_discover_walks_up_parent_dirs(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("llm:\n  effort: max\n", encoding="utf-8")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    config = PatchwrightConfig.discover(start=deep)
    assert config.llm.effort == "max"


def test_discover_stops_at_git_boundary(tmp_path: Path) -> None:
    """If a .git directory is in the path, don't walk past it (project boundary)."""
    (tmp_path / CONFIG_FILENAME).write_text("llm:\n  effort: max\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    deep = project / "src" / "pkg"
    deep.mkdir(parents=True)

    config = PatchwrightConfig.discover(start=deep)
    # Should be defaults — the tmp_path config is outside the .git boundary.
    assert config == PatchwrightConfig()


def test_discover_returns_defaults_when_no_file_anywhere(tmp_path: Path) -> None:
    config = PatchwrightConfig.discover(start=tmp_path)
    assert config == PatchwrightConfig()


# --------------------------------------------------------------------------- dump round trip


def test_dump_yaml_round_trips() -> None:
    original = PatchwrightConfig()
    serialized = original.dump_yaml()
    # Should be valid YAML
    parsed = yaml.safe_load(serialized)
    assert isinstance(parsed, dict)
    # Round-trip equality
    reloaded = PatchwrightConfig.model_validate(parsed)
    assert reloaded == original


def test_dump_yaml_includes_all_sections() -> None:
    serialized = PatchwrightConfig().dump_yaml()
    for section in ("llm", "embargo", "sandbox", "review", "conventions", "repo"):
        assert f"{section}:" in serialized


# --------------------------------------------------------------------------- is_local_url


def test_is_local_url_matches_localhost() -> None:
    config = PatchwrightConfig()
    assert config.is_local_url("http://localhost:11434/v1")
    assert config.is_local_url("http://127.0.0.1:8080")
    assert config.is_local_url("https://[::1]/api")


def test_is_local_url_rejects_remote() -> None:
    config = PatchwrightConfig()
    assert not config.is_local_url("https://api.openai.com/v1")
    assert not config.is_local_url("http://example.com")
    assert not config.is_local_url(None)
    assert not config.is_local_url("")


def test_is_local_url_respects_custom_allowlist() -> None:
    config = PatchwrightConfig.model_validate(
        {"embargo": {"local_hosts": ["my-vpc-vllm.internal"]}}
    )
    assert config.is_local_url("https://my-vpc-vllm.internal:8000/v1")
    assert not config.is_local_url("http://localhost/v1")
