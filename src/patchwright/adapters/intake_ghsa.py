"""GHSAIntakeAdapter — GitHub Security Advisory JSON normalizer (AEG-444).

Accepts the GitHub REST API GHSA shape (e.g. the response from
`gh api /advisories/GHSA-xxxx-xxxx-xxxx` or `/repos/{o}/{r}/security-advisories/...`)
and normalizes to the M6.1 `Report` type.

Field map (GHSA → Report):

    ghsa_id            -> source_id          ("ghsa:GHSA-...")
    summary            -> summary            (required)
    description        -> details
    published_at       -> published          (ISO-8601)
    updated_at         -> modified           (ISO-8601)
    vulnerabilities[]  -> affected[Package]  (one Package per vulnerability)
    references[]       -> references[Reference]  (type defaulted to "ADVISORY")
    severity (string)  -> severity[Severity]  (from cvss.vector_string when present)
    credits[]          -> ReporterIdentity   (first credit; user.login -> handle)

T10 enforcement: the Report's reporter_id is always the pseudonymous output
of pseudonymize_reporter_id; the real handle is returned separately as
ReporterIdentity. The pseudonym seed is the GHSA user.login (deterministic
across reports from the same reporter) or, when no credits are present, the
ghsa_id itself.

FR-IN-5 enforcement: every well-formedness failure raises IntakeError with
a structured message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from patchwright.core.intake import (
    IntakeError,
    Package,
    ParseResult,
    Reference,
    Report,
    ReporterIdentity,
    Severity,
    pseudonymize_reporter_id,
)


@dataclass
class GHSAIntakeAdapter:
    """GitHub Security Advisory JSON intake.

    Mirrors the JSONIntakeAdapter dataclass style. Lazy-imported by
    `core.intake.default_intake_adapter` so the module stays out of the
    package import path until an operator selects it.
    """

    name: str = "ghsa"

    def parse(self, raw: bytes) -> ParseResult:
        payload = _load_ghsa_payload(raw)
        report_kwargs = _report_kwargs_from_ghsa(payload)
        identity = _identity_from_ghsa(payload, fallback_seed=report_kwargs["source_id"])
        report_kwargs["reporter_id"] = identity.reporter_id

        try:
            report = Report(**report_kwargs)
        except ValidationError as exc:
            raise IntakeError(f"validation failed: {exc}") from exc

        return ParseResult(
            report=report,
            identity=identity if _identity_has_real_info(identity) else None,
        )


# --------------------------------------------------------------------------- decode + parse


def _load_ghsa_payload(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntakeError(f"invalid encoding: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IntakeError(f"invalid JSON: {exc.msg} at line {exc.lineno} col {exc.colno}") from exc

    if not isinstance(payload, dict):
        raise IntakeError(
            f"invalid JSON: expected object at top level, got {type(payload).__name__}"
        )
    return payload


# --------------------------------------------------------------------------- field mapping


def _report_kwargs_from_ghsa(payload: dict[str, Any]) -> dict[str, Any]:
    ghsa_id = _require_str(payload, "ghsa_id")
    summary = _require_str(payload, "summary")

    return {
        "source_id": f"ghsa:{ghsa_id}",
        "source_adapter": "ghsa",
        "summary": summary,
        "details": _optional_str(payload, "description", default=""),
        "affected": _affected_from_ghsa(payload.get("vulnerabilities", [])),
        "references": _references_from_ghsa(payload.get("references", [])),
        "severity": _severity_from_ghsa(payload),
        "published": _optional_iso_date(payload, "published_at"),
        "modified": _optional_iso_date(payload, "updated_at"),
    }


def _require_str(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise IntakeError(f"missing field {key!r}")
    value = payload[key]
    if not isinstance(value, str):
        raise IntakeError(f"field {key!r} must be a string, got {type(value).__name__}")
    return value


def _optional_str(payload: dict[str, Any], key: str, *, default: str) -> str:
    if key not in payload or payload[key] is None:
        return default
    value = payload[key]
    if not isinstance(value, str):
        raise IntakeError(f"field {key!r} must be a string, got {type(value).__name__}")
    return value


def _optional_iso_date(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise IntakeError(f"invalid {key} date: must be string, got {type(value).__name__}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntakeError(f"invalid {key} date: {exc}") from exc
    return value


# --------------------------------------------------------------------------- affected


def _affected_from_ghsa(entries: Any) -> tuple[Package, ...]:
    """GHSA `vulnerabilities[]` carries one entry per affected package, each with
    `package.{ecosystem, name}`, `vulnerable_version_range`, optional
    `first_patched_version.identifier`. We collapse onto one Package per entry."""
    if not isinstance(entries, list):
        raise IntakeError(f"field 'vulnerabilities' must be an array, got {type(entries).__name__}")

    out: list[Package] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise IntakeError(f"vulnerabilities[{i}] must be an object")
        pkg = entry.get("package")
        if not isinstance(pkg, dict):
            raise IntakeError(f"vulnerabilities[{i}].package must be an object")

        ranges: list[str] = []
        vrange = entry.get("vulnerable_version_range")
        if isinstance(vrange, str) and vrange.strip():
            ranges.append(vrange)
        elif vrange is not None and not isinstance(vrange, str):
            raise IntakeError(
                f"vulnerabilities[{i}].vulnerable_version_range must be a string, "
                f"got {type(vrange).__name__}"
            )

        first_patched = entry.get("first_patched_version")
        if isinstance(first_patched, dict):
            ident = first_patched.get("identifier")
            if isinstance(ident, str) and ident.strip():
                ranges.append(f"first_patched:{ident}")

        try:
            out.append(
                Package(
                    ecosystem=_require_str(pkg, "ecosystem"),
                    name=_require_str(pkg, "name"),
                    purl=pkg.get("purl"),
                    versions=(),
                    ranges=tuple(ranges),
                )
            )
        except ValidationError as exc:
            raise IntakeError(f"validation failed: vulnerabilities[{i}]: {exc}") from exc
    return tuple(out)


# --------------------------------------------------------------------------- references


_REFERENCE_TYPE_BY_URL = (
    ("/commit/", "FIX"),
    ("/pull/", "FIX"),
    ("/security/advisories/", "ADVISORY"),
    ("nvd.nist.gov/vuln", "ADVISORY"),
    ("cve.mitre.org", "ADVISORY"),
)


def _classify_reference(url: str) -> str:
    """GHSA references[] don't carry a type; infer a sensible OSV type from the URL.
    Default to ADVISORY which is the safe fallback for security-related links."""
    lowered = url.lower()
    for needle, kind in _REFERENCE_TYPE_BY_URL:
        if needle in lowered:
            return kind
    return "WEB"


def _references_from_ghsa(entries: Any) -> tuple[Reference, ...]:
    if not isinstance(entries, list):
        raise IntakeError(f"field 'references' must be an array, got {type(entries).__name__}")
    out: list[Reference] = []
    for i, entry in enumerate(entries):
        if isinstance(entry, str):
            url = entry
        elif isinstance(entry, dict):
            if "url" not in entry:
                raise IntakeError(f"references[{i}] missing 'url'")
            url_val = entry["url"]
            if not isinstance(url_val, str):
                raise IntakeError(f"references[{i}].url must be a string")
            url = url_val
        else:
            raise IntakeError(
                f"references[{i}] must be a string or object, got {type(entry).__name__}"
            )
        try:
            out.append(Reference(type=_classify_reference(url), url=url))  # type: ignore[arg-type]
        except ValidationError as exc:
            raise IntakeError(f"validation failed: references[{i}]: {exc}") from exc
    return tuple(out)


# --------------------------------------------------------------------------- severity


def _severity_from_ghsa(payload: dict[str, Any]) -> tuple[Severity, ...]:
    """GHSA carries a CVSS vector under `cvss.vector_string` (or `cvss_severities[]`
    on the repo-advisory variant). We surface the vector as a CVSS_V3 / CVSS_V4
    Severity, picking the type from the vector's `CVSS:N.N/` prefix."""
    cvss = payload.get("cvss")
    if isinstance(cvss, dict):
        vector = cvss.get("vector_string")
        if isinstance(vector, str) and vector.strip():
            return (_build_severity(vector),)

    cvss_severities = payload.get("cvss_severities")
    if isinstance(cvss_severities, list):
        out: list[Severity] = []
        for i, entry in enumerate(cvss_severities):
            if not isinstance(entry, dict):
                raise IntakeError(f"cvss_severities[{i}] must be an object")
            vector = entry.get("vector_string")
            if isinstance(vector, str) and vector.strip():
                out.append(_build_severity(vector))
        return tuple(out)

    return ()


def _build_severity(vector: str) -> Severity:
    cvss_type = "CVSS_V4" if vector.startswith("CVSS:4") else "CVSS_V3"
    try:
        return Severity(type=cvss_type, score=vector)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise IntakeError(f"validation failed: severity {vector!r}: {exc}") from exc


# --------------------------------------------------------------------------- reporter / T10


def _identity_from_ghsa(payload: dict[str, Any], *, fallback_seed: str) -> ReporterIdentity:
    """Extract reporter identity from GHSA `credits[]`. Always returns a
    ReporterIdentity with the pseudonym in reporter_id; only carries real
    identity fields when the credit had structured user information."""
    real_name: str | None = None
    real_handle: str | None = None
    real_email: str | None = None
    context: str | None = None

    credits = payload.get("credits")
    if isinstance(credits, list) and credits:
        first = credits[0]
        if not isinstance(first, dict):
            raise IntakeError("credits[0] must be an object")
        user = first.get("user")
        if user is not None and not isinstance(user, dict):
            raise IntakeError("credits[0].user must be an object")

        if isinstance(user, dict):
            login = user.get("login")
            name = user.get("name")
            email = user.get("email")
            if login is not None and not isinstance(login, str):
                raise IntakeError("credits[0].user.login must be a string")
            if name is not None and not isinstance(name, str):
                raise IntakeError("credits[0].user.name must be a string")
            if email is not None and not isinstance(email, str):
                raise IntakeError("credits[0].user.email must be a string")
            real_handle = f"@{login}" if login else None
            real_name = name or None
            real_email = email or None
            context = "credits[0].user"
    elif credits is not None and not isinstance(credits, list):
        raise IntakeError(f"field 'credits' must be an array, got {type(credits).__name__}")

    seed = real_email or real_handle or real_name or fallback_seed
    pseudonym = pseudonymize_reporter_id(seed)

    return ReporterIdentity(
        reporter_id=pseudonym,
        real_name=real_name,
        real_email=real_email,
        real_handle=real_handle,
        context=context,
    )


def _identity_has_real_info(identity: ReporterIdentity) -> bool:
    return any(
        x is not None for x in (identity.real_name, identity.real_email, identity.real_handle)
    )


__all__ = ["GHSAIntakeAdapter"]
