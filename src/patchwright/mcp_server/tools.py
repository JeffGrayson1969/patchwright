"""MCP tool implementations (AEG-379, M7).

Plain, JSON-returning functions wrapping the PatchWright core so they can be
unit-tested without an MCP transport. `server.py` registers thin FastMCP
wrappers over these. Each returns a dict with an `ok` flag; failures are
returned as structured `{ok: False, error: ...}` rather than raised, so the
calling agent (Claude Code, Cursor, Cline) gets an actionable message.

Fully wired: intake_report, get_status, explain_case, triage_case,
reproduce_poc, generate_patch_plan. Deferred (structured stub): apply_patch
(cross-checker gate + PR open — needs the repo-effects layer) and
draft_advisory (CSAF/OpenVEX — P2 per PRD §A.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.cases import list_all_cases, load_case
from patchwright.core.config import PatchwrightConfig
from patchwright.core.evidence import render
from patchwright.core.intake import ingest
from patchwright.core.journal_crypto import cipher_for_reading
from patchwright.core.orchestrator import case_root_paths, drive
from patchwright.core.registry import Registry
from patchwright.core.sandbox import SandboxRunner

Json = dict[str, Any]


def _err(msg: str) -> Json:
    return {"ok": False, "error": msg}


def _bad_case_id(case_id: str) -> Json | None:
    """Reject a case_id that could escape the persistence root. Defense-in-depth at
    the MCP boundary: a prompt-injected host could pass a traversal path."""
    if not case_id or "/" in case_id or "\\" in case_id or ".." in case_id:
        return _err(f"invalid case_id: {case_id!r}")
    return None


# --------------------------------------------------------------------------- pure tools


def intake_report(*, root: Path, config: PatchwrightConfig, raw: str, source: str) -> Json:
    """Ingest a raw report (source: 'json' | 'ghsa') and open a case."""
    try:
        case = ingest(raw.encode("utf-8"), source=source, root=root, config=config)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}")
    return {
        "ok": True,
        "case_id": case.id,
        "state": case.state,
        "artifacts": sorted(a.kind for a in case.artifacts),
    }


def get_status(*, root: Path, config: PatchwrightConfig, case_id: str | None = None) -> Json:
    """Return one case's state, or a list of all cases when case_id is None."""
    cipher = cipher_for_reading()
    if case_id:
        if (bad := _bad_case_id(case_id)) is not None:
            return bad
        try:
            rec = load_case(case_id, root, cipher=cipher)
        except FileNotFoundError as exc:
            return _err(str(exc))
        return {
            "ok": True,
            "case_id": rec.case.id,
            "state": rec.case.state,
            "entries": len(rec.entries),
            "last_kind": rec.entries[-1].kind if rec.entries else None,
        }
    cases = list_all_cases(root, cipher=cipher)
    return {
        "ok": True,
        "cases": [
            {"case_id": c.case.id, "state": c.case.state, "entries": len(c.entries)} for c in cases
        ],
    }


def explain_case(*, root: Path, config: PatchwrightConfig, case_id: str) -> Json:
    """Return the markdown evidence packet for a case."""
    if (bad := _bad_case_id(case_id)) is not None:
        return bad
    cipher = cipher_for_reading()
    try:
        rec = load_case(case_id, root, cipher=cipher)
    except FileNotFoundError as exc:
        return _err(str(exc))
    store = ArtifactStore(case_root_paths(root, case_id)["artifacts_dir"]).read_only()
    return {"ok": True, "case_id": case_id, "markdown": render(rec.case, rec.entries, store)}


# --------------------------------------------------------------------------- agent tools


def _run_step(root: Path, config: PatchwrightConfig, case_id: str, registry: Registry) -> Json:
    """Drive one case with a single-agent registry, reporting the resulting state.

    The registry holds exactly one advancing agent, so drive() applies that one
    transition (or pauses if the case isn't in the handled state) — giving the
    named per-step MCP tool semantics."""
    if (bad := _bad_case_id(case_id)) is not None:
        return bad
    try:
        case = drive(case_id, registry, root, config=config)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}")
    return {"ok": True, "case_id": case_id, "state": case.state}


def triage_case(*, root: Path, config: PatchwrightConfig, case_id: str) -> Json:
    """Run triage on a case (INTAKE -> TRIAGED | REJECTED)."""
    from patchwright.agents.triage import TriageAgent  # noqa: PLC0415
    from patchwright.providers.factory import provider_from_config  # noqa: PLC0415

    try:
        provider = provider_from_config(config)
    except Exception as exc:
        return _err(f"LLM provider not configured: {exc}")
    registry = Registry()
    registry.register(TriageAgent(provider=provider))
    return _run_step(root, config, case_id, registry)


def reproduce_poc(*, root: Path, config: PatchwrightConfig, case_id: str) -> Json:
    """Reproduce a case's PoC in the sandbox (TRIAGED -> REPRODUCED | NOT_REPRODUCIBLE)."""
    from patchwright.agents.reproduce import ReproduceAgent  # noqa: PLC0415

    sandbox = _sandbox_from_config(config)
    registry = Registry()
    registry.register(ReproduceAgent(sandbox=sandbox, case_root=root / "scratch"))
    return _run_step(root, config, case_id, registry)


def generate_patch_plan(
    *, root: Path, config: PatchwrightConfig, case_id: str, repo_root: str
) -> Json:
    """Generate a natural-language patch plan (REPRODUCED -> PATCH_PROPOSED).

    The LLM emits a plan only — never a diff (PRD §10.1 two-phase patch commitment)."""
    from patchwright.agents.patch_plan import PatchPlanAgent  # noqa: PLC0415
    from patchwright.providers.factory import provider_from_config  # noqa: PLC0415

    try:
        provider = provider_from_config(config)
    except Exception as exc:
        return _err(f"LLM provider not configured: {exc}")
    registry = Registry()
    registry.register(PatchPlanAgent(provider=provider, repo_root=Path(repo_root)))
    return _run_step(root, config, case_id, registry)


# --------------------------------------------------------------------------- deferred tools


def apply_patch(*, root: Path, config: PatchwrightConfig, case_id: str) -> Json:
    """Apply an approved patch plan and open a draft PR (PATCH_PROPOSED -> AWAITING_REVIEW).

    Deferred: this step runs the cross-checker gate (T9) + deterministic codemod +
    the PR-opening TransitionEffect, which require the repo-adapter/effects layer to
    be wired through the MCP surface. Tracked as a follow-up; use the CLI patch flow
    meanwhile."""
    return {
        "ok": False,
        "status": "not_wired",
        "note": (
            "apply_patch (cross-checker gate + codemod + draft-PR effect) is not yet "
            "exposed via MCP; use the CLI patch-apply/effects flow. Tracked as follow-up."
        ),
    }


def draft_advisory(*, root: Path, config: PatchwrightConfig, case_id: str) -> Json:
    """Draft a CSAF + OpenVEX advisory from a patch. P2 (PRD §A.1 marks this a stub)."""
    return {
        "ok": False,
        "status": "p2",
        "note": "Advisory drafting (CSAF/OpenVEX) is a Phase 2 capability.",
    }


# --------------------------------------------------------------------------- helpers


def _sandbox_from_config(config: PatchwrightConfig) -> SandboxRunner:
    if config.sandbox.backend == "gvisor":
        from patchwright.sandboxes.gvisor import GVisorSandbox  # noqa: PLC0415

        return GVisorSandbox()
    from patchwright.sandboxes.docker import DockerSandbox  # noqa: PLC0415

    return DockerSandbox()
