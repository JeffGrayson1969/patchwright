"""`patchwright serve --mcp` — run the MCP server over stdio (AEG-379, M7)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from patchwright.core.config import PatchwrightConfig


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "serve",
        help="Run PatchWright as an MCP server (primary integration surface).",
        description=(
            "Exposes PatchWright's 8 tools over MCP so any MCP-aware host "
            "(Claude Code, Cursor, Cline, ...) can drive a case. Stdio transport."
        ),
    )
    p.add_argument("--mcp", action="store_true", help="Run the MCP server (stdio).")
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Persistence root containing journal/ and artifacts/ dirs. Defaults to cwd.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to patchwright.yaml. Defaults to built-in config.",
    )
    p.set_defaults(func=cmd_serve)


def cmd_serve(args: argparse.Namespace) -> int:
    if not args.mcp:
        print("error: only --mcp (stdio) transport is supported in P1", file=sys.stderr)
        return 2

    root: Path = args.root or Path.cwd()
    config = PatchwrightConfig.load(args.config) if args.config else PatchwrightConfig()

    # Imported lazily so the rest of the CLI doesn't pay the MCP import cost.
    from patchwright.mcp_server.server import build_server  # noqa: PLC0415

    server = build_server(root, config)
    server.run()  # FastMCP defaults to stdio transport
    return 0
