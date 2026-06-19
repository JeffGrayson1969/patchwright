"""JSONIntakeAdapter (AEG-443 / M6.2) — OSV-Schema JSON pass-through.

Covers FR-IN-1, FR-IN-2, FR-IN-5 and the T10 regression.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from patchwright.adapters.intake_json import JSONIntakeAdapter
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

# --------------------------------------------------------------------------- fixtures


def _minimal_osv() -> dict[str, Any]:
    return {
        "id": "GHSA-aaaa-bbbb-cccc",
        "summary": "Prototype pollution in foo",
    }


def _full_osv() -> dict[str, Any]:
    return {
        "id": "GHSA-aaaa-bbbb-cccc",
        "summary": "Prototype pollution in foo",
        "details": "Long form description.",
        "published": "2026-01-01T00:00:00Z",
        "modified": "2026-01-02T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "foo", "purl": "pkg:npm/foo"},
                "versions": ["1.0.0", "1.0.1"],
                "ranges": [
                    {
                        "type": "SEMVER",
                        "events": [{"introduced": "1.0.0"}, {"fixed": "1.0.2"}],
                    }
                ],
            }
        ],
        "references": [
            {"type": "ADVISORY", "url": "https://example.com/advisory"},
            {"type": "FIX", "url": "https://example.com/commit/abc123"},
        ],
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
        "credits": [
            {
                "name": "Alice",
                "contact": ["mailto:alice@example.com", "https://github.com/alice"],
                "type": "REPORTER",
            }
        ],
    }


def _as_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------------------- Protocol conformance


def test_satisfies_intake_adapter_protocol() -> None:
    assert isinstance(JSONIntakeAdapter(), IntakeAdapter)


def test_adapter_name_is_json() -> None:
    assert JSONIntakeAdapter.name == "json"
    assert JSONIntakeAdapter().name == "json"


def test_default_factory_returns_json_adapter() -> None:
    """The M6.1 factory placeholder was a raise — confirm it now hands back
    a JSONIntakeAdapter on the 'json' route."""
    adapter = default_intake_adapter("json", PatchwrightConfig())
    assert isinstance(adapter, JSONIntakeAdapter)


# --------------------------------------------------------------------------- happy path


def test_minimal_payload_parses() -> None:
    result = JSONIntakeAdapter().parse(_as_bytes(_minimal_osv()))
    assert isinstance(result, ParseResult)
    assert isinstance(result.report, Report)
    assert result.report.source_id == "GHSA-aaaa-bbbb-cccc"
    assert result.report.source_adapter == "json"
    assert result.report.summary == "Prototype pollution in foo"
    assert result.report.details == ""
    assert result.report.affected == ()
    assert result.report.published is None
    assert result.identity is None  # no reporter info


def test_full_payload_round_trip() -> None:
    result = JSONIntakeAdapter().parse(_as_bytes(_full_osv()))
    r = result.report

    assert r.source_id == "GHSA-aaaa-bbbb-cccc"
    assert r.details == "Long form description."
    assert r.published == "2026-01-01T00:00:00Z"

    assert r.affected == (
        Package(
            ecosystem="npm",
            name="foo",
            purl="pkg:npm/foo",
            versions=("1.0.0", "1.0.1"),
            ranges=(
                json.dumps(
                    {"type": "SEMVER", "events": [{"introduced": "1.0.0"}, {"fixed": "1.0.2"}]},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        ),
    )

    assert r.references == (
        Reference(type="ADVISORY", url="https://example.com/advisory"),
        Reference(type="FIX", url="https://example.com/commit/abc123"),
    )
    assert r.severity == (
        Severity(type="CVSS_V3", score="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),
    )

    assert result.identity is not None
    assert result.identity.real_name == "Alice"
    assert result.identity.real_email == "alice@example.com"
    assert result.identity.real_handle == "@alice"
    assert result.identity.context == "credits[0]"


def test_reporter_extension_preferred_over_credits() -> None:
    """PatchWright-specific top-level `reporter` wins over OSV `credits[]`."""
    payload = _full_osv()
    payload["reporter"] = {"name": "Bob", "email": "bob@example.com", "handle": "@bob"}
    result = JSONIntakeAdapter().parse(_as_bytes(payload))
    assert result.identity is not None
    assert result.identity.real_name == "Bob"
    assert result.identity.real_email == "bob@example.com"
    assert result.identity.context == "reporter"


def test_ranges_passthrough_string_form() -> None:
    payload = _minimal_osv()
    payload["affected"] = [
        {"package": {"ecosystem": "PyPI", "name": "foo"}, "ranges": ["raw-string-range"]}
    ]
    r = JSONIntakeAdapter().parse(_as_bytes(payload)).report
    assert r.affected[0].ranges == ("raw-string-range",)


# --------------------------------------------------------------------------- FR-IN-5 failure modes


def test_invalid_utf8_raises() -> None:
    with pytest.raises(IntakeError, match="invalid encoding"):
        JSONIntakeAdapter().parse(b"\xff\xfe\x00bad")


def test_invalid_json_raises() -> None:
    with pytest.raises(IntakeError, match="invalid JSON"):
        JSONIntakeAdapter().parse(b"{not json")


def test_top_level_array_rejected() -> None:
    with pytest.raises(IntakeError, match="expected object at top level"):
        JSONIntakeAdapter().parse(b"[]")


def test_missing_id_raises() -> None:
    payload = _minimal_osv()
    del payload["id"]
    with pytest.raises(IntakeError, match="missing field 'id'"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_missing_summary_raises() -> None:
    payload = _minimal_osv()
    del payload["summary"]
    with pytest.raises(IntakeError, match="missing field 'summary'"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_invalid_published_date_raises() -> None:
    payload = _minimal_osv()
    payload["published"] = "not-a-date"
    with pytest.raises(IntakeError, match="invalid published date"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_invalid_modified_date_raises() -> None:
    payload = _minimal_osv()
    payload["modified"] = "still-not-a-date"
    with pytest.raises(IntakeError, match="invalid modified date"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_published_wrong_type_raises() -> None:
    payload = _minimal_osv()
    payload["published"] = 12345
    with pytest.raises(IntakeError, match="invalid published date"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_summary_wrong_type_raises() -> None:
    payload = _minimal_osv()
    payload["summary"] = 42
    with pytest.raises(IntakeError, match="summary"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_affected_not_array_raises() -> None:
    payload = _minimal_osv()
    payload["affected"] = {"package": {"ecosystem": "PyPI", "name": "foo"}}
    with pytest.raises(IntakeError, match="'affected' must be an array"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_affected_missing_package_raises() -> None:
    payload = _minimal_osv()
    payload["affected"] = [{"versions": ["1.0.0"]}]
    with pytest.raises(IntakeError, match=r"affected\[0\].package"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_unknown_reference_type_validation_fails() -> None:
    payload = _minimal_osv()
    payload["references"] = [{"type": "MADE_UP", "url": "https://x"}]
    with pytest.raises(IntakeError, match="validation failed"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_unknown_severity_type_validation_fails() -> None:
    payload = _minimal_osv()
    payload["severity"] = [{"type": "CVSS_V2", "score": "x"}]
    with pytest.raises(IntakeError, match="validation failed"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_summary_too_long_validation_fails() -> None:
    payload = _minimal_osv()
    payload["summary"] = "x" * 501  # Report caps summary at 500
    with pytest.raises(IntakeError, match="validation failed"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


# --------------------------------------------------------------------------- T10 regression


def test_reporter_id_is_pseudonymous() -> None:
    """Report.reporter_id is the pseudonym, not the raw identity."""
    result = JSONIntakeAdapter().parse(_as_bytes(_full_osv()))
    assert result.report.reporter_id.startswith("reporter-")
    assert "alice" not in result.report.reporter_id
    assert "@" not in result.report.reporter_id


def test_real_email_never_in_report_bytes() -> None:
    """The hard T10 invariant: serializing the Report must not leak the
    real reporter email. ReporterIdentity carries it instead, in a
    separable artifact the future M3-encrypt step will wrap."""
    result = JSONIntakeAdapter().parse(_as_bytes(_full_osv()))
    report_bytes = result.report.model_dump_json().encode("utf-8")
    assert b"alice@example.com" not in report_bytes
    assert b"@alice" not in report_bytes
    assert b"Alice" not in report_bytes

    assert result.identity is not None
    assert result.identity.real_email == "alice@example.com"


def test_reporter_id_matches_identity() -> None:
    result = JSONIntakeAdapter().parse(_as_bytes(_full_osv()))
    assert result.identity is not None
    assert result.report.reporter_id == result.identity.reporter_id


def test_pseudonym_is_deterministic_across_parses() -> None:
    """Same reporter -> same pseudonym across reports (enables FR-TR-2 trust scoring)."""
    a = JSONIntakeAdapter().parse(_as_bytes(_full_osv())).report.reporter_id
    b = JSONIntakeAdapter().parse(_as_bytes(_full_osv())).report.reporter_id
    assert a == b
    assert a == pseudonymize_reporter_id("alice@example.com")


def test_pseudonym_seeded_from_source_id_when_no_reporter() -> None:
    """Without reporter info, the pseudonym is derived from source_id so the
    Report still carries a stable reporter_id."""
    result = JSONIntakeAdapter().parse(_as_bytes(_minimal_osv()))
    assert result.identity is None
    assert result.report.reporter_id == pseudonymize_reporter_id("GHSA-aaaa-bbbb-cccc")


def test_identity_dropped_when_credits_present_but_empty() -> None:
    """A credits entry with no actual identity fields still parses; the
    ReporterIdentity has only a pseudonym and is dropped on return."""
    payload = _minimal_osv()
    payload["credits"] = [{"name": None, "contact": []}]
    result = JSONIntakeAdapter().parse(_as_bytes(payload))
    assert result.identity is None


def test_reporter_extension_email_pseudonymizes_consistently() -> None:
    payload = _minimal_osv()
    payload["reporter"] = {"email": "bob@example.com"}
    result = JSONIntakeAdapter().parse(_as_bytes(payload))
    assert result.report.reporter_id == pseudonymize_reporter_id("bob@example.com")


# --------------------------------------------------------------------------- identity edge cases


def test_credits_not_array_raises() -> None:
    payload = _minimal_osv()
    payload["credits"] = "alice"
    with pytest.raises(IntakeError, match="'credits' must be an array"):
        JSONIntakeAdapter().parse(_as_bytes(payload))


def test_credits_entry_not_object_raises() -> None:
    payload = _minimal_osv()
    payload["credits"] = ["alice"]
    with pytest.raises(IntakeError, match=r"credits\[0\] must be an object"):
        JSONIntakeAdapter().parse(_as_bytes(payload))
