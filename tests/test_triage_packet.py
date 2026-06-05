"""TriagePacket Pydantic model — validation and field constraints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from patchwright.models.triage import DedupMatch, TriageDisposition, TriagePacket


def _minimal_packet() -> dict[str, object]:
    return {
        "case_id": "case-x",
        "summary": "X",
        "claim_type": "unspecified",
        "confidence": 0.5,
        "disposition": "advance",
        "rationale": "tests",
    }


def test_minimal_packet_validates() -> None:
    p = TriagePacket.model_validate(_minimal_packet())
    assert p.disposition is TriageDisposition.ADVANCE
    assert p.dedup_matches == []
    assert p.affected_components == []


def test_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        TriagePacket.model_validate({**_minimal_packet(), "confidence": 1.5})
    with pytest.raises(ValidationError):
        TriagePacket.model_validate({**_minimal_packet(), "confidence": -0.1})


def test_unknown_disposition_rejected() -> None:
    with pytest.raises(ValidationError):
        TriagePacket.model_validate({**_minimal_packet(), "disposition": "nuke"})


def test_extra_fields_rejected() -> None:
    bad = {**_minimal_packet(), "secret_field": "abc"}
    with pytest.raises(ValidationError):
        TriagePacket.model_validate(bad)


def test_dedup_matches_round_trip() -> None:
    p = TriagePacket.model_validate(
        {
            **_minimal_packet(),
            "dedup_matches": [
                {"identifier": "CVE-2024-1234", "similarity": 0.92, "rationale": "same fn"}
            ],
        }
    )
    assert len(p.dedup_matches) == 1
    match = p.dedup_matches[0]
    assert isinstance(match, DedupMatch)
    assert match.identifier == "CVE-2024-1234"


def test_packet_is_frozen() -> None:
    p = TriagePacket.model_validate(_minimal_packet())
    with pytest.raises(ValidationError):
        p.confidence = 0.99  # type: ignore[misc]
