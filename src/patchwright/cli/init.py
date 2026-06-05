"""`patchwright init` — write a default patchwright.yaml.

Default config is the conservative path (PRD NFR-M-2): human-in-loop at every
checkpoint, 90-day embargo, Anthropic provider with effort='high', Docker
sandbox, ruff formatting, pytest test command.

Refuses to overwrite an existing file unless --force is passed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from patchwright.core.config import CONFIG_FILENAME, PatchwrightConfig


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "init",
        help="Write a default patchwright.yaml to the current directory.",
        description=(
            "Generates a patchwright.yaml with conservative defaults (human-in-loop, "
            "90-day embargo, Anthropic + adaptive thinking + effort=high)."
        ),
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory to write patchwright.yaml in. Default: current directory.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing patchwright.yaml.",
    )
    p.set_defaults(func=cmd_init)


def cmd_init(args: argparse.Namespace) -> int:
    root: Path = args.root or Path.cwd()
    target = root / CONFIG_FILENAME

    if target.exists() and not args.force:
        print(
            f"refusing to overwrite existing {target} — pass --force to replace",
            file=sys.stderr,
        )
        return 1

    config = PatchwrightConfig()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_HEADER + config.dump_yaml(), encoding="utf-8")
    print(f"wrote {target}", file=sys.stderr)
    return 0


_HEADER = """# PatchWright project configuration.
# Documented sections: llm, embargo, sandbox, review, conventions.
# See https://github.com/JeffGrayson1969/patchwright for field reference.
#
# embargo.mode='strict' refuses any LLM call whose endpoint is not in
# embargo.local_hosts. Pair with llm.provider='openai_compat' and
# llm.base_url='http://localhost:11434/v1' (Ollama) for fully-local triage.

"""
