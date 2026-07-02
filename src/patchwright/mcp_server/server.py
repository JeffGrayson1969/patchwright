"""FastMCP server exposing PatchWright's 8 tools over stdio (AEG-379, M7).

`patchwright serve --mcp` runs this so any MCP-aware host (Claude Code, Cursor,
Cline, Continue, Aider, Codex CLI) can drive a case through the pipeline —
PatchWright's *primary* integration surface (PRD §A.1). Each tool closes over a
fixed persistence `root` + `config` resolved at startup; the host supplies only
the per-call arguments.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from patchwright.core.config import PatchwrightConfig
from patchwright.mcp_server import tools

TOOL_NAMES = (
    "intake_report",
    "triage_case",
    "reproduce_poc",
    "generate_patch_plan",
    "apply_patch",
    "draft_advisory",
    "get_status",
    "explain_case",
)


def build_server(root: Path, config: PatchwrightConfig) -> FastMCP:
    """Construct the FastMCP server with all 8 tools bound to (root, config)."""
    mcp = FastMCP("patchwright")

    @mcp.tool()
    def intake_report(raw: str, source: str = "json") -> dict[str, object]:
        """Ingest a raw vulnerability report and open a case. source: 'json' | 'ghsa'."""
        return tools.intake_report(root=root, config=config, raw=raw, source=source)

    @mcp.tool()
    def triage_case(case_id: str) -> dict[str, object]:
        """Run triage on a case (INTAKE -> TRIAGED | REJECTED)."""
        return tools.triage_case(root=root, config=config, case_id=case_id)

    @mcp.tool()
    def reproduce_poc(case_id: str) -> dict[str, object]:
        """Reproduce a case's PoC in the sandbox (TRIAGED -> REPRODUCED | NOT_REPRODUCIBLE)."""
        return tools.reproduce_poc(root=root, config=config, case_id=case_id)

    @mcp.tool()
    def generate_patch_plan(case_id: str, repo_root: str) -> dict[str, object]:
        """Generate a natural-language patch plan (REPRODUCED -> PATCH_PROPOSED)."""
        return tools.generate_patch_plan(
            root=root, config=config, case_id=case_id, repo_root=repo_root
        )

    @mcp.tool()
    def apply_patch(case_id: str) -> dict[str, object]:
        """Apply an approved plan and open a draft PR (PATCH_PROPOSED -> AWAITING_REVIEW)."""
        return tools.apply_patch(root=root, config=config, case_id=case_id)

    @mcp.tool()
    def draft_advisory(case_id: str) -> dict[str, object]:
        """Draft a CSAF + OpenVEX advisory from a patch (P2)."""
        return tools.draft_advisory(root=root, config=config, case_id=case_id)

    @mcp.tool()
    def get_status(case_id: str | None = None) -> dict[str, object]:
        """Get one case's state, or list all cases when case_id is omitted."""
        return tools.get_status(root=root, config=config, case_id=case_id)

    @mcp.tool()
    def explain_case(case_id: str) -> dict[str, object]:
        """Return the markdown evidence packet (decision tree) for a case."""
        return tools.explain_case(root=root, config=config, case_id=case_id)

    return mcp
