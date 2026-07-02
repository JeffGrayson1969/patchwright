"""PocSpec + ReproLog — validation rules and frozen-model invariants (AEG-462).

These guard the artifact-schema promises:
  - PocSpec.cmd has min_length=1 (no empty argv)
  - PocSpec.timeout_seconds is bounded (0 < t <= 600)
  - Both models are frozen + extra="forbid" (immutable journal artifacts)
  - ReproLog stdio tails reject anything over 4096 chars
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from patchwright.models.poc import PocSpec
from patchwright.models.repro import ReproLog

# --------------------------------------------------------------------------- PocSpec


def test_poc_spec_minimal_valid() -> None:
    spec = PocSpec(cmd=("true",))
    assert spec.cmd == ("true",)
    assert spec.image is None
    assert spec.script is None
    assert spec.timeout_seconds == 60.0
    assert spec.schema_version == "1"


def test_poc_spec_rejects_empty_cmd() -> None:
    with pytest.raises(ValidationError):
        PocSpec(cmd=())


def test_poc_spec_rejects_zero_timeout() -> None:
    with pytest.raises(ValidationError):
        PocSpec(cmd=("true",), timeout_seconds=0.0)


def test_poc_spec_rejects_oversize_timeout() -> None:
    with pytest.raises(ValidationError):
        PocSpec(cmd=("true",), timeout_seconds=601.0)


def test_poc_spec_accepts_max_timeout() -> None:
    spec = PocSpec(cmd=("true",), timeout_seconds=600.0)
    assert spec.timeout_seconds == 600.0


def test_poc_spec_frozen() -> None:
    spec = PocSpec(cmd=("true",))
    with pytest.raises(ValidationError):
        spec.timeout_seconds = 30.0  # type: ignore[misc]


def test_poc_spec_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PocSpec(cmd=("true",), bogus_field="x")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- ReproLog


def test_repro_log_minimal_valid() -> None:
    log = ReproLog(
        case_id="case-x",
        verdict="reproduced",
        reason="ok",
        image="alpine:3.20",
        sandbox_name="fake",
    )
    assert log.verdict == "reproduced"
    assert log.cmd == ()
    assert log.exit_code == -1
    assert log.stdout_tail == ""
    assert log.stderr_tail == ""
    assert log.timed_out is False
    assert log.network_enabled is False
    assert log.poc_artifact_id is None
    assert log.schema_version == "1"


def test_repro_log_rejects_invalid_verdict() -> None:
    with pytest.raises(ValidationError):
        ReproLog(
            case_id="case-x",
            verdict="maybe",  # type: ignore[arg-type]
            reason="ok",
            image="alpine:3.20",
            sandbox_name="fake",
        )


def test_repro_log_stdout_tail_max_length_enforced() -> None:
    with pytest.raises(ValidationError):
        ReproLog(
            case_id="case-x",
            verdict="reproduced",
            reason="ok",
            image="alpine:3.20",
            sandbox_name="fake",
            stdout_tail="x" * 4097,
        )


def test_repro_log_stderr_tail_max_length_enforced() -> None:
    with pytest.raises(ValidationError):
        ReproLog(
            case_id="case-x",
            verdict="reproduced",
            reason="ok",
            image="alpine:3.20",
            sandbox_name="fake",
            stderr_tail="x" * 4097,
        )


def test_repro_log_frozen() -> None:
    log = ReproLog(
        case_id="case-x",
        verdict="reproduced",
        reason="ok",
        image="alpine:3.20",
        sandbox_name="fake",
    )
    with pytest.raises(ValidationError):
        log.exit_code = 0  # type: ignore[misc]


def test_repro_log_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ReproLog(
            case_id="case-x",
            verdict="reproduced",
            reason="ok",
            image="alpine:3.20",
            sandbox_name="fake",
            unknown_key="x",  # type: ignore[call-arg]
        )
