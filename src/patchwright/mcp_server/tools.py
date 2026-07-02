"""MCP tool implementations (AEG-379, M7).

Plain, JSON-returning functions wrapping the PatchWright core so they can be
unit-tested without an MCP transport. `server.py` registers thin FastMCP
wrappers over these. Each returns a dict with an `ok` flag; failures are
returned as structured `{ok: False, error: ...}` rather than raised, so the
calling agent (Claude Code, Cursor, Cline) gets an actionable message.

Fully wired: intake_report, get_status, explain_case, triage_case,
reproduce_poc, generate_patch_plan, apply_patch (cross-checker gate + codemod +
draft-PR effect; gated behind the operator's --allow-mutations). Stub:
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
from patchwright.core.journal import Journal
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


def _has_human_approval(root: Path, case_id: str) -> bool:
    """True iff the case has an operator-recorded 'approve' human_decision.

    Written only by `patchwright review` (operator action), so a prompt-injected
    host cannot fabricate it — the per-case half of apply_patch's approval gate
    (CLAUDE.md #8: human approval at every outward transition)."""
    journal = Journal(case_root_paths(root, case_id)["journal_dir"], cipher=cipher_for_reading())
    return any(
        e.kind == "human_decision" and e.payload.get("decision") == "approve"
        for e in journal.read()
    )


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


# ------------------------------------------------------------------ mutating / deferred tools


def apply_patch(  # noqa: PLR0911 — one early-return per guard/step
    *,
    root: Path,
    config: PatchwrightConfig,
    case_id: str,
    workspace_root: str,
    allow_mutations: bool,
) -> Json:
    """Apply an approved patch plan and open a draft PR (PATCH_PROPOSED -> AWAITING_REVIEW).

    Runs the cross-checker gate (T9) -> deterministic codemod + tests -> draft-PR
    TransitionEffect. This is the only MCP tool with an outward-facing, mutating
    side-effect, so it has TWO independent, host-uncontrollable gates (CLAUDE.md #8):
      1. `allow_mutations` — a server-startup capability (`serve --mcp --allow-mutations`)
      2. a per-case operator `approve` on record (from `patchwright review`)
    A prompt-injected host controls neither. Opens a *draft* PR only — never merges."""
    if not allow_mutations:
        return {
            "ok": False,
            "status": "mutations_disabled",
            "note": (
                "apply_patch performs an outward-facing action (opens a draft PR) and is "
                "disabled. The operator must restart the server with "
                "`patchwright serve --mcp --allow-mutations` to enable it."
            ),
        }
    if (bad := _bad_case_id(case_id)) is not None:
        return bad
    if not _has_human_approval(root, case_id):
        return {
            "ok": False,
            "status": "approval_required",
            "note": (
                "apply_patch requires an operator 'approve' on record for this case. "
                "Run `patchwright review` and approve before applying via MCP — a host "
                "cannot self-approve an outward-facing PR (CLAUDE.md #8)."
            ),
        }
    ws = Path(workspace_root)
    if not ws.is_dir():
        return _err(f"workspace_root does not exist or is not a directory: {workspace_root}")

    from patchwright.agents.cross_checker import CrossCheckerAgent  # noqa: PLC0415
    from patchwright.agents.patch_apply import PatchApplyAgent  # noqa: PLC0415
    from patchwright.core.orchestrator import TransitionEffects  # noqa: PLC0415
    from patchwright.core.repo_effects import register_default_effects  # noqa: PLC0415
    from patchwright.providers.factory import build_cross_checker  # noqa: PLC0415

    try:
        cross_checker_provider = build_cross_checker(config)
    except Exception as exc:
        return _err(f"cross-checker LLM provider not configured: {exc}")

    registry = Registry()
    registry.register(CrossCheckerAgent(provider=cross_checker_provider))
    registry.register(
        PatchApplyAgent(
            repo_root=ws,
            sandbox=_sandbox_from_config(config),
            case_root=root / "scratch",
            config=config,
        )
    )
    effects = TransitionEffects()
    register_default_effects(effects)

    try:
        case = drive(case_id, registry, root, config=config, effects=effects, workspace_root=ws)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}")
    return {"ok": True, "case_id": case_id, "state": case.state}


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
