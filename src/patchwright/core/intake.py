"""IntakeAdapter Protocol — the boundary between inbound reports and PatchWright.

Concrete adapters live in `patchwright.adapters.intake_*`:
  - intake_json.py  (M6.2 / AEG-443) — generic OSV-Schema JSON
  - intake_ghsa.py  (M6.3 / AEG-444) — GitHub Security Advisory normalizer

PRD §6.1 commitment (FR-IN-1, FR-IN-2): every adapter normalizes its input to
the OSV-Schema-shaped `Report` type defined here. The original input bytes are
preserved by the caller (M6.3 `ingest()`) as a separate `raw_input` artifact so
the audit trail is intact.

T10 mitigation: every adapter MUST return a `Report` whose `reporter_id` is
the pseudonymous output of `pseudonymize_reporter_id(real_id)`. The real
identity (if known) is returned separately as `ReporterIdentity`, which the
caller stores in its own artifact for future encryption by M3-encrypt
(AEG-376). The structural separation means a forgetful adapter cannot leak
PII into the `Report` artifact that triage + the journal read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from patchwright.core.hashing import sha256_b16

if TYPE_CHECKING:
    from patchwright.core.config import PatchwrightConfig


class IntakeError(Exception):
    """Raised when input is malformed (FR-IN-5).

    Adapters surface every well-formedness failure via this exception with a
    structured message. The high-level `ingest()` entry point in M6.3 catches
    it and returns the error to the operator instead of opening a half-formed
    case.
    """


# --------------------------------------------------------------------------- OSV-Schema subset


class Package(BaseModel):
    """One affected package, OSV-Schema-shaped.

    `ecosystem` follows OSV's ecosystem enum (PyPI, npm, Maven, Go, …).
    `versions` lists specific known-vulnerable versions; `ranges` carries
    range expressions (semver/ecosystem-specific) when known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ecosystem: str
    name: str
    purl: str | None = None
    versions: tuple[str, ...] = ()
    ranges: tuple[str, ...] = ()


class Reference(BaseModel):
    """One external reference URL, OSV-Schema-shaped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal[
        "WEB",
        "ADVISORY",
        "REPORT",
        "FIX",
        "PACKAGE",
        "ARTICLE",
        "DETECTION",
        "DISCUSSION",
        "EVIDENCE",
        "GIT",
    ]
    url: str


class Severity(BaseModel):
    """One severity scoring, OSV-Schema-shaped. P1 supports CVSS v3 and v4."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["CVSS_V3", "CVSS_V4"]
    score: str
    """CVSS vector string, e.g. 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H'."""


# --------------------------------------------------------------------------- Report


class Report(BaseModel):
    """Normalized intake report. Subset of OSV-Schema relevant to P1.

    Every adapter normalizes to this shape. Stored as the `raw_report`
    artifact when `ingest()` opens a case; triage + downstream agents read
    it via the read-only store.

    T10: `reporter_id` is ALWAYS the pseudonymous output of
    `pseudonymize_reporter_id`. Real identity goes in a separate
    ReporterIdentity artifact — never on this object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"

    source_id: str = Field(
        description="Canonical id from the source, e.g. 'ghsa:GHSA-xxxx-xxxx-xxxx'."
    )
    source_adapter: str = Field(
        description="Which IntakeAdapter produced this Report. Used for provenance + replay."
    )

    summary: str = Field(min_length=1, max_length=500)
    details: str = ""

    affected: tuple[Package, ...] = ()
    references: tuple[Reference, ...] = ()
    severity: tuple[Severity, ...] = ()

    published: str | None = Field(
        default=None,
        description="ISO-8601 timestamp the source reports the advisory was published, or None.",
    )
    modified: str | None = Field(
        default=None,
        description="ISO-8601 timestamp the source last modified the advisory, or None.",
    )

    reporter_id: str = Field(
        description=(
            "PSEUDONYMOUS reporter id — always 'reporter-<12hex>'. Real identity, if "
            "known, is captured in a separate ReporterIdentity artifact (T10)."
        )
    )


# --------------------------------------------------------------------------- ReporterIdentity


class ReporterIdentity(BaseModel):
    """Real reporter identity. Stored as a separate artifact (kind='reporter_identity').

    M3-encrypt (AEG-376) will wrap this artifact with at-rest encryption. Until
    then it's stored plaintext but structurally separate from `Report`, so a
    bug in any single adapter cannot leak PII into the public artifact chain.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reporter_id: str = Field(description="The same pseudonymous id as Report.reporter_id.")

    real_name: str | None = None
    real_email: str | None = None
    real_handle: str | None = Field(
        default=None,
        description="Source-specific handle, e.g. '@user' on GitHub.",
    )
    context: str | None = Field(
        default=None,
        description="Provenance string, e.g. 'ghsa.credits[0].user.login'.",
    )


# --------------------------------------------------------------------------- Protocol


class ParseResult(NamedTuple):
    """What an IntakeAdapter returns.

    `identity` is None when the input does not carry reporter information
    (some scanner adapters, or anonymized inbound). The Report's
    `reporter_id` is still set — to a pseudonym derived from the source id
    in that case.
    """

    report: Report
    identity: ReporterIdentity | None


@runtime_checkable
class IntakeAdapter(Protocol):
    """A backend that parses raw inbound bytes into a normalized Report.

    Implementations:
      - adapters/intake_json.py  (M6.2 — generic OSV-Schema JSON)
      - adapters/intake_ghsa.py  (M6.3 — GitHub Security Advisory)

    Contract:
      - `parse(raw)` returns a `ParseResult` on success.
      - `parse(raw)` raises `IntakeError` (FR-IN-5) on any well-formedness
        failure: encoding error, JSON parse error, missing required field,
        type error, invalid date format, schema mismatch.
      - The Report's `reporter_id` MUST be the pseudonymous output of
        `pseudonymize_reporter_id`. Real identity goes only in the
        returned `ReporterIdentity` (T10 enforcement).
    """

    name: str
    """Stable identifier — recorded in journal entries + Report.source_adapter."""

    def parse(self, raw: bytes) -> ParseResult: ...


# --------------------------------------------------------------------------- T10 helper


def pseudonymize_reporter_id(real_id: str) -> str:
    """Map a real reporter identifier to a deterministic pseudonym (T10).

    Same `real_id` → same pseudonym across reports, enabling future
    rule-based trust scoring (FR-TR-2) without leaking identity into any
    journaled artifact. Empty input is still hashed — adapters that have
    no reporter info should pass a stable source-derived seed instead of
    skipping pseudonymization.

    Output shape: `'reporter-<12hex>'` (mirrors `stable_case_id`).
    """
    digest = sha256_b16(real_id.encode("utf-8"))
    return "reporter-" + digest[7:19]


# --------------------------------------------------------------------------- factory


def default_intake_adapter(name: str, config: PatchwrightConfig) -> IntakeAdapter:
    """Instantiate the configured IntakeAdapter.

    M6.1 ships contracts only; no backend is wired yet. AEG-443 lands the JSON
    adapter; AEG-444 lands the GHSA adapter. Until then any call raises
    `IntakeError` with a clear pointer at the follow-up tickets.
    """
    raise IntakeError(
        f"intake adapter {name!r} not yet implemented — "
        "'json' lands in AEG-443 (M6.2), 'ghsa' lands in AEG-444 (M6.3). "
        "Track: https://linear.app/aegisq/issue/AEG-377"
    )


__all__ = [
    "IntakeAdapter",
    "IntakeError",
    "Package",
    "ParseResult",
    "Reference",
    "Report",
    "ReporterIdentity",
    "Severity",
    "default_intake_adapter",
    "pseudonymize_reporter_id",
]
