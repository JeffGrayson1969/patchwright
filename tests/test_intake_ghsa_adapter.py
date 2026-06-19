"""GHSAIntakeAdapter (AEG-444 / M6.3) — GitHub Security Advisory normalizer.

Covers FR-IN-1, FR-IN-2, FR-IN-5 and the T10 regression.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from patchwright.adapters.intake_ghsa import GHSAIntakeAdapter
from patchwright.core.config import PatchwrightConfig
from patchwright.core.intake import (
    IntakeAdapter,
    IntakeError,
    Package,
    ParseResult,
    Reference,
    Report,
    Severity,
    default_intake_adapter,
    pseudonymize_reporter_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "intake" / "sample_ghsa.json"


def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _fixture_dict() -> dict[str, Any]:
    return json.loads(_fixture_bytes())


def _as_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------------------- Protocol conformance


def test_satisfies_intake_adapter_protocol() -> None:
    assert isinstance(GHSAIntakeAdapter(), IntakeAdapter)


def test_adapter_name_is_ghsa() -> None:
    assert GHSAIntakeAdapter().name == "ghsa"


def test_default_factory_returns_ghsa_adapter() -> None:
    adapter = default_intake_adapter("ghsa", PatchwrightConfig())
    assert isinstance(adapter, GHSAIntakeAdapter)


# --------------------------------------------------------------------------- happy path


def test_fixture_parses_to_full_report() -> None:
    result = GHSAIntakeAdapter().parse(_fixture_bytes())
    assert isinstance(result, ParseResult)
    r = result.report
    assert isinstance(r, Report)

    assert r.source_id == "ghsa:GHSA-rprw-h62v-c2w7"
    assert r.source_adapter == "ghsa"
    assert r.summary == "PyYAML insecure deserialization via yaml.load"
    assert r.details.startswith("PyYAML's `yaml.load`")
    assert r.published == "2024-08-12T16:00:00Z"
    assert r.modified == "2024-08-13T09:15:00Z"


def test_fixture_affected_collapses_vulnerability_entries() -> None:
    r = GHSAIntakeAdapter().parse(_fixture_bytes()).report
    assert r.affected == (
        Package(
            ecosystem="pip",
            name="pyyaml",
            purl=None,
            versions=(),
            ranges=("< 5.4", "first_patched:5.4"),
        ),
    )


def test_fixture_references_classified_from_urls() -> None:
    """GHSA references don't carry types; the adapter infers them per URL substring."""
    r = GHSAIntakeAdapter().parse(_fixture_bytes()).report
    assert r.references == (
        Reference(
            type="ADVISORY",
            url="https://github.com/yaml/pyyaml/security/advisories/GHSA-rprw-h62v-c2w7",
        ),
        Reference(type="FIX", url="https://github.com/yaml/pyyaml/commit/abc1234567890def"),
        Reference(type="ADVISORY", url="https://nvd.nist.gov/vuln/detail/CVE-2024-XXXX"),
        Reference(type="WEB", url="https://example.com/blog/post-about-yaml"),
    )


def test_fixture_severity_from_cvss_vector() -> None:
    r = GHSAIntakeAdapter().parse(_fixture_bytes()).report
    assert r.severity == (
        Severity(type="CVSS_V3", score="CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H"),
    )


def test_cvss_v4_vector_picks_v4_type() -> None:
    payload = _fixture_dict()
    payload["cvss"] = {"vector_string": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H"}
    r = GHSAIntakeAdapter().parse(_as_bytes(payload)).report
    assert r.severity == (
        Severity(
            type="CVSS_V4",
            score="CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H",
        ),
    )


def test_fixture_identity_from_credits() -> None:
    result = GHSAIntakeAdapter().parse(_fixture_bytes())
    assert result.identity is not None
    assert result.identity.real_name == "Alice Q. Researcher"
    assert result.identity.real_email == "alice@example.com"
    assert result.identity.real_handle == "@alice-researcher"
    assert result.identity.context == "credits[0].user"


# --------------------------------------------------------------------------- FR-IN-5 failure modes


def test_invalid_utf8_raises() -> None:
    with pytest.raises(IntakeError, match="invalid encoding"):
        GHSAIntakeAdapter().parse(b"\xff\xfe\x00bad")


def test_invalid_json_raises() -> None:
    with pytest.raises(IntakeError, match="invalid JSON"):
        GHSAIntakeAdapter().parse(b"{not json")


def test_top_level_array_rejected() -> None:
    with pytest.raises(IntakeError, match="expected object at top level"):
        GHSAIntakeAdapter().parse(b"[]")


def test_missing_ghsa_id_raises() -> None:
    payload = _fixture_dict()
    del payload["ghsa_id"]
    with pytest.raises(IntakeError, match="missing field 'ghsa_id'"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


def test_missing_summary_raises() -> None:
    payload = _fixture_dict()
    del payload["summary"]
    with pytest.raises(IntakeError, match="missing field 'summary'"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


def test_invalid_published_at_raises() -> None:
    payload = _fixture_dict()
    payload["published_at"] = "not-a-date"
    with pytest.raises(IntakeError, match="invalid published_at date"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


def test_vulnerabilities_not_array_raises() -> None:
    payload = _fixture_dict()
    payload["vulnerabilities"] = {}
    with pytest.raises(IntakeError, match="'vulnerabilities' must be an array"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


def test_vulnerability_missing_package_raises() -> None:
    payload = _fixture_dict()
    payload["vulnerabilities"] = [{"vulnerable_version_range": "< 1.0"}]
    with pytest.raises(IntakeError, match=r"vulnerabilities\[0\].package"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


def test_reference_missing_url_raises() -> None:
    payload = _fixture_dict()
    payload["references"] = [{}]
    with pytest.raises(IntakeError, match=r"references\[0\] missing 'url'"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


def test_credits_not_array_raises() -> None:
    payload = _fixture_dict()
    payload["credits"] = "alice"
    with pytest.raises(IntakeError, match="'credits' must be an array"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


def test_credits_user_not_object_raises() -> None:
    payload = _fixture_dict()
    payload["credits"] = [{"user": "alice"}]
    with pytest.raises(IntakeError, match=r"credits\[0\].user must be an object"):
        GHSAIntakeAdapter().parse(_as_bytes(payload))


# --------------------------------------------------------------------------- empty / minimal


def test_minimal_payload_only_required_fields() -> None:
    """ghsa_id + summary are the only must-haves for a Report."""
    payload = {"ghsa_id": "GHSA-aaaa-bbbb-cccc", "summary": "minimal"}
    r = GHSAIntakeAdapter().parse(_as_bytes(payload)).report
    assert r.source_id == "ghsa:GHSA-aaaa-bbbb-cccc"
    assert r.summary == "minimal"
    assert r.details == ""
    assert r.affected == ()
    assert r.references == ()
    assert r.severity == ()
    assert r.published is None
    assert r.modified is None


def test_credits_without_user_field_keeps_identity_anonymous() -> None:
    payload = _fixture_dict()
    payload["credits"] = [{"type": "reporter"}]
    result = GHSAIntakeAdapter().parse(_as_bytes(payload))
    assert result.identity is None


# --------------------------------------------------------------------------- T10 regression


def test_reporter_id_is_pseudonymous() -> None:
    result = GHSAIntakeAdapter().parse(_fixture_bytes())
    assert result.report.reporter_id.startswith("reporter-")
    assert "alice" not in result.report.reporter_id
    assert "@" not in result.report.reporter_id


def test_real_identity_never_in_report_bytes() -> None:
    """T10 invariant: serializing the Report must not leak the real reporter
    handle, name, or email. Those live only on ReporterIdentity."""
    result = GHSAIntakeAdapter().parse(_fixture_bytes())
    report_bytes = result.report.model_dump_json().encode("utf-8")
    assert b"alice@example.com" not in report_bytes
    assert b"alice-researcher" not in report_bytes
    assert b"Alice Q. Researcher" not in report_bytes

    assert result.identity is not None
    assert result.identity.real_email == "alice@example.com"
    assert result.identity.real_handle == "@alice-researcher"


def test_pseudonym_seeded_from_email_when_present() -> None:
    """Email is the most stable seed when available."""
    r = GHSAIntakeAdapter().parse(_fixture_bytes()).report
    assert r.reporter_id == pseudonymize_reporter_id("alice@example.com")


def test_pseudonym_seeded_from_handle_when_no_email() -> None:
    payload = _fixture_dict()
    payload["credits"][0]["user"]["email"] = None
    r = GHSAIntakeAdapter().parse(_as_bytes(payload)).report
    assert r.reporter_id == pseudonymize_reporter_id("@alice-researcher")


def test_pseudonym_seeded_from_ghsa_id_when_no_reporter() -> None:
    payload = _fixture_dict()
    del payload["credits"]
    r = GHSAIntakeAdapter().parse(_as_bytes(payload)).report
    assert r.reporter_id == pseudonymize_reporter_id("ghsa:GHSA-rprw-h62v-c2w7")
