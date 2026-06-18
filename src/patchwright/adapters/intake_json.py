"""JSONIntakeAdapter — generic OSV-Schema JSON pass-through (AEG-443).

Maps an OSV-Schema-shaped JSON payload directly onto the M6.1 `Report`
type. The intent: anyone who can produce OSV-shaped JSON (scanners,
custom scripts, manual paste) has a working intake without writing an
adapter.

Field map (OSV → Report):

    id          -> source_id          (passthrough; required)
    summary     -> summary             (required)
    details     -> details             (optional, default "")
    published   -> published           (optional, ISO-8601)
    modified    -> modified            (optional, ISO-8601)
    affected[]  -> affected[Package]   (flattened from OSV's {package, versions, ranges})
    references[]-> references[Reference]
    severity[]  -> severity[Severity]
    credits[]   -> ReporterIdentity    (first credit; OSV-standard)
    reporter    -> ReporterIdentity    (PatchWright extension; preferred when present)

T10 enforcement (CLAUDE.md hard "do not"):
    - Report.reporter_id is ALWAYS the pseudonymous output of
      pseudonymize_reporter_id; the real identity (if any) is returned
      separately as ReporterIdentity. A forgetful caller cannot leak PII
      into the Report artifact.
    - When no reporter info is in the payload, we seed the pseudonym
      from `source_id` so the Report still carries a stable id.

FR-IN-5 enforcement: every well-formedness failure raises IntakeError
with a structured message. Adapters never return invalid Reports.
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
class JSONIntakeAdapter:
    """Generic OSV-Schema JSON intake.

    The contract is the IntakeAdapter Protocol (PEP 544 structural).
    Mirrors the `GitHubRepoAdapter` style in `adapters/repo_github.py`
    — plain (not-frozen) dataclass so `name` satisfies the Protocol's
    settable-variable requirement.
    """

    name: str = "json"

    def parse(self, raw: bytes) -> ParseResult:
        payload = _load_osv_payload(raw)
        report_kwargs = _report_kwargs_from_osv(payload)
        identity = _identity_from_osv(payload, fallback_seed=report_kwargs["source_id"])
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


def _load_osv_payload(raw: bytes) -> dict[str, Any]:
    """Decode bytes -> dict. Raises IntakeError for encoding or JSON faults."""
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


def _report_kwargs_from_osv(payload: dict[str, Any]) -> dict[str, Any]:
    """Build Report kwargs except reporter_id (set after identity is known)."""
    source_id = _require_str(payload, "id")
    summary = _require_str(payload, "summary")

    return {
        "source_id": source_id,
        "source_adapter": "json",
        "summary": summary,
        "details": _optional_str(payload, "details", default=""),
        "affected": _affected_from_osv(payload.get("affected", [])),
        "references": _references_from_osv(payload.get("references", [])),
        "severity": _severity_from_osv(payload.get("severity", [])),
        "published": _optional_iso_date(payload, "published"),
        "modified": _optional_iso_date(payload, "modified"),
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
    # Permissive: accept either bare ISO-8601 or trailing 'Z' (RFC-3339).
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntakeError(f"invalid {key} date: {exc}") from exc
    return value


def _affected_from_osv(entries: Any) -> tuple[Package, ...]:
    """Flatten OSV's {package, versions, ranges} into our flat Package shape.

    OSV is verbose: `affected[].package.{ecosystem, name, purl}` + sibling
    `versions[]` + `ranges[]`. We collapse onto one Package per entry.
    """
    if not isinstance(entries, list):
        raise IntakeError(f"field 'affected' must be an array, got {type(entries).__name__}")

    out: list[Package] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise IntakeError(f"affected[{i}] must be an object")
        pkg = entry.get("package")
        if not isinstance(pkg, dict):
            raise IntakeError(f"affected[{i}].package must be an object")
        try:
            out.append(
                Package(
                    ecosystem=_require_str(pkg, "ecosystem"),
                    name=_require_str(pkg, "name"),
                    purl=pkg.get("purl"),
                    versions=tuple(_str_list(entry.get("versions", []), f"affected[{i}].versions")),
                    ranges=tuple(_range_strings(entry.get("ranges", []), f"affected[{i}].ranges")),
                )
            )
        except ValidationError as exc:
            raise IntakeError(f"validation failed: affected[{i}]: {exc}") from exc
    return tuple(out)


def _references_from_osv(entries: Any) -> tuple[Reference, ...]:
    if not isinstance(entries, list):
        raise IntakeError(f"field 'references' must be an array, got {type(entries).__name__}")
    out: list[Reference] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise IntakeError(f"references[{i}] must be an object")
        try:
            out.append(
                Reference(
                    type=_require_str(entry, "type"),  # type: ignore[arg-type]
                    url=_require_str(entry, "url"),
                )
            )
        except ValidationError as exc:
            raise IntakeError(f"validation failed: references[{i}]: {exc}") from exc
    return tuple(out)


def _severity_from_osv(entries: Any) -> tuple[Severity, ...]:
    if not isinstance(entries, list):
        raise IntakeError(f"field 'severity' must be an array, got {type(entries).__name__}")
    out: list[Severity] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise IntakeError(f"severity[{i}] must be an object")
        try:
            out.append(
                Severity(
                    type=_require_str(entry, "type"),  # type: ignore[arg-type]
                    score=_require_str(entry, "score"),
                )
            )
        except ValidationError as exc:
            raise IntakeError(f"validation failed: severity[{i}]: {exc}") from exc
    return tuple(out)


def _str_list(entries: Any, where: str) -> list[str]:
    if not isinstance(entries, list):
        raise IntakeError(f"{where} must be an array, got {type(entries).__name__}")
    out: list[str] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, str):
            raise IntakeError(f"{where}[{i}] must be a string, got {type(entry).__name__}")
        out.append(entry)
    return out


def _range_strings(entries: Any, where: str) -> list[str]:
    """OSV ranges are structured ({type, events: [...]}) — we preserve them
    as opaque JSON strings since the Report model uses tuple[str, ...]. The
    triage agent gets the full structure back via json.loads when needed."""
    if not isinstance(entries, list):
        raise IntakeError(f"{where} must be an array, got {type(entries).__name__}")
    out: list[str] = []
    for i, entry in enumerate(entries):
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            out.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))
        else:
            raise IntakeError(
                f"{where}[{i}] must be a string or object, got {type(entry).__name__}"
            )
    return out


# --------------------------------------------------------------------------- reporter / T10


def _identity_from_osv(payload: dict[str, Any], *, fallback_seed: str) -> ReporterIdentity:
    """Extract reporter identity from the PatchWright `reporter` extension
    (preferred) or OSV `credits[]` (fallback). The returned ReporterIdentity
    always carries the pseudonym in reporter_id (T10)."""
    real_name: str | None = None
    real_email: str | None = None
    real_handle: str | None = None
    context: str | None = None

    reporter = payload.get("reporter")
    credits = payload.get("credits")

    if isinstance(reporter, dict):
        real_name = _opt_str_field(reporter, "name", "reporter.name")
        real_email = _opt_str_field(reporter, "email", "reporter.email")
        real_handle = _opt_str_field(reporter, "handle", "reporter.handle")
        context = "reporter"
    elif isinstance(credits, list) and credits:
        first = credits[0]
        if not isinstance(first, dict):
            raise IntakeError("credits[0] must be an object")
        real_name = _opt_str_field(first, "name", "credits[0].name")
        contacts = first.get("contact", ())
        if contacts and not isinstance(contacts, list):
            raise IntakeError("credits[0].contact must be an array")
        real_email, real_handle = _split_contacts(contacts or [])
        context = "credits[0]"
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


def _opt_str_field(payload: dict[str, Any], key: str, where: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise IntakeError(f"{where} must be a string, got {type(value).__name__}")
    return value


def _split_contacts(contacts: Any) -> tuple[str | None, str | None]:
    """OSV credits.contact is a list of 'mailto:foo@bar' / 'https://github.com/foo' style URIs."""
    email: str | None = None
    handle: str | None = None
    for c in contacts:
        if not isinstance(c, str):
            raise IntakeError("credits[0].contact entries must be strings")
        if email is None and c.startswith("mailto:"):
            email = c.removeprefix("mailto:")
        elif handle is None and c.startswith(("https://github.com/", "https://gitlab.com/")):
            handle = "@" + c.rstrip("/").rsplit("/", 1)[-1]
    return email, handle


def _identity_has_real_info(identity: ReporterIdentity) -> bool:
    return any(
        x is not None
        for x in (identity.real_name, identity.real_email, identity.real_handle)
    )


__all__ = ["JSONIntakeAdapter"]
