"""IntakeAdapter Protocol + Report / ReporterIdentity / Package / Reference / Severity
schema sanity + pseudonymize_reporter_id determinism + factory error path (AEG-377.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from patchwright.core.config import IntakeConfig, PatchwrightConfig
from patchwright.core.intake import (
    IntakeAdapter,
    IntakeError,
    Package,
    ParseResult,
    Reference,
    Report,
    ReporterIdentity,
    Severity,
    default_intake_adapter,
    pseudonymize_reporter_id,
)

# --------------------------------------------------------------------------- Package


def test_package_round_trip() -> None:
    p = Package(ecosystem="PyPI", name="django", versions=("3.2.0", "3.2.1"))
    assert Package.model_validate_json(p.model_dump_json()) == p


def test_package_frozen() -> None:
    p = Package(ecosystem="PyPI", name="django")
    with pytest.raises(ValidationError):
        p.name = "other"  # type: ignore[misc]


def test_package_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        Package.model_validate({"ecosystem": "PyPI", "name": "django", "unknown": 1})


def test_package_defaults() -> None:
    p = Package(ecosystem="PyPI", name="django")
    assert p.purl is None
    assert p.versions == ()
    assert p.ranges == ()


# --------------------------------------------------------------------------- Reference


def test_reference_round_trip() -> None:
    r = Reference(type="ADVISORY", url="https://example.com/advisory")
    assert Reference.model_validate_json(r.model_dump_json()) == r


def test_reference_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        Reference.model_validate({"type": "MADE_UP", "url": "https://x"})


# --------------------------------------------------------------------------- Severity


def test_severity_round_trip() -> None:
    s = Severity(type="CVSS_V3", score="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert Severity.model_validate_json(s.model_dump_json()) == s


def test_severity_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        Severity.model_validate({"type": "CVSS_V2", "score": "x"})


# --------------------------------------------------------------------------- Report


def _ok_report() -> Report:
    return Report(
        source_id="ghsa:GHSA-aaaa-bbbb-cccc",
        source_adapter="ghsa",
        summary="Prototype pollution in foo",
        details="Long form description.",
        affected=(Package(ecosystem="npm", name="foo", versions=("1.0.0",)),),
        references=(Reference(type="ADVISORY", url="https://x/advisory"),),
        severity=(Severity(type="CVSS_V3", score="CVSS:3.1/..."),),
        published="2026-01-01T00:00:00Z",
        modified="2026-01-02T00:00:00Z",
        reporter_id="reporter-abc123def456",
    )


def test_report_round_trip() -> None:
    r = _ok_report()
    assert Report.model_validate_json(r.model_dump_json()) == r


def test_report_frozen() -> None:
    r = _ok_report()
    with pytest.raises(ValidationError):
        r.source_id = "other"  # type: ignore[misc]


def test_report_extra_forbidden() -> None:
    bad = _ok_report().model_dump(mode="json")
    bad["unknown"] = "x"
    with pytest.raises(ValidationError):
        Report.model_validate(bad)


def test_report_schema_version_pinned() -> None:
    bad = _ok_report().model_dump(mode="json")
    bad["schema_version"] = "2"
    with pytest.raises(ValidationError):
        Report.model_validate(bad)


def test_report_summary_required_non_empty() -> None:
    with pytest.raises(ValidationError):
        Report.model_validate(
            {
                "source_id": "x",
                "source_adapter": "json",
                "summary": "",
                "reporter_id": "reporter-x",
            }
        )


def test_report_optional_fields_default_to_empty() -> None:
    r = Report(
        source_id="x",
        source_adapter="json",
        summary="x",
        reporter_id="reporter-x",
    )
    assert r.affected == ()
    assert r.references == ()
    assert r.severity == ()
    assert r.published is None
    assert r.modified is None
    assert r.details == ""


# --------------------------------------------------------------------------- ReporterIdentity


def test_reporter_identity_round_trip() -> None:
    ri = ReporterIdentity(
        reporter_id="reporter-abc",
        real_email="alice@example.com",
        real_handle="@alice",
        context="ghsa.credits[0].user.login",
    )
    assert ReporterIdentity.model_validate_json(ri.model_dump_json()) == ri


def test_reporter_identity_frozen() -> None:
    ri = ReporterIdentity(reporter_id="reporter-x")
    with pytest.raises(ValidationError):
        ri.real_email = "x@y"  # type: ignore[misc]


def test_reporter_identity_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        ReporterIdentity.model_validate({"reporter_id": "r", "unknown": True})


def test_reporter_identity_all_optionals_default_to_none() -> None:
    ri = ReporterIdentity(reporter_id="reporter-x")
    assert ri.real_name is None
    assert ri.real_email is None
    assert ri.real_handle is None
    assert ri.context is None


# --------------------------------------------------------------------------- ParseResult


def test_parse_result_is_a_named_tuple() -> None:
    r = _ok_report()
    ri = ReporterIdentity(reporter_id=r.reporter_id, real_email="x@y")
    result = ParseResult(report=r, identity=ri)
    # tuple access
    assert result[0] == r
    assert result[1] == ri
    # named access
    assert result.report == r
    assert result.identity == ri


def test_parse_result_allows_none_identity() -> None:
    r = _ok_report()
    result = ParseResult(report=r, identity=None)
    assert result.identity is None


# --------------------------------------------------------------------------- Protocol


def _make_stub_adapter() -> object:
    class _Stub:
        name = "stub"

        def parse(self, raw: bytes) -> ParseResult:
            return ParseResult(report=_ok_report(), identity=None)

    return _Stub()


def test_full_stub_satisfies_protocol() -> None:
    assert isinstance(_make_stub_adapter(), IntakeAdapter)


def test_partial_stub_fails_protocol_check() -> None:
    class _Partial:
        name = "partial"

    assert not isinstance(_Partial(), IntakeAdapter)


# --------------------------------------------------------------------------- T10 pseudonymization


def test_pseudonymize_is_deterministic() -> None:
    assert pseudonymize_reporter_id("alice@example.com") == pseudonymize_reporter_id(
        "alice@example.com"
    )


def test_pseudonymize_different_inputs_yield_different_outputs() -> None:
    a = pseudonymize_reporter_id("alice@example.com")
    b = pseudonymize_reporter_id("bob@example.com")
    assert a != b


def test_pseudonymize_output_shape() -> None:
    p = pseudonymize_reporter_id("alice@example.com")
    assert p.startswith("reporter-")
    suffix = p.removeprefix("reporter-")
    assert len(suffix) == 12
    assert all(c in "0123456789abcdef" for c in suffix)


def test_pseudonymize_handles_empty_input() -> None:
    """Empty input is still hashed — adapters with no reporter pass a stable
    source-derived seed instead of skipping pseudonymization."""
    assert pseudonymize_reporter_id("").startswith("reporter-")


def test_pseudonymize_never_returns_real_id_substring() -> None:
    """T10 regression: an email address must never appear inside the pseudonym."""
    real = "alice@example.com"
    p = pseudonymize_reporter_id(real)
    assert "alice" not in p
    assert "example" not in p


# --------------------------------------------------------------------------- factory


def test_default_intake_adapter_raises_until_adapters_land() -> None:
    """Exit criterion for AEG-377.1: factory raises a clear error pointing at
    the follow-up tickets until M6.2 / M6.3 land."""
    with pytest.raises(IntakeError, match="AEG-443"):
        default_intake_adapter("json", PatchwrightConfig())
    with pytest.raises(IntakeError, match="AEG-444"):
        default_intake_adapter("ghsa", PatchwrightConfig())


def test_intake_error_is_an_exception() -> None:
    assert issubclass(IntakeError, Exception)


# --------------------------------------------------------------------------- IntakeConfig


def test_intake_config_defaults() -> None:
    c = IntakeConfig()
    assert c.default_adapter == "json"


def test_intake_config_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        IntakeConfig.model_validate({"default_adapter": "json", "unknown": True})


def test_intake_config_rejects_unknown_adapter() -> None:
    with pytest.raises(ValidationError):
        IntakeConfig.model_validate({"default_adapter": "vince"})


def test_patchwright_config_includes_intake_section() -> None:
    c = PatchwrightConfig()
    assert c.intake.default_adapter == "json"
