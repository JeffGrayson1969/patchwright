from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from patchwright import __version__
from patchwright.cli import explain, hello, init, journal, review
from patchwright.cli import list as list_cmd


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="patchwright", description="PatchWright runtime CLI.")
    p.add_argument("--version", action="version", version=f"patchwright {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    hello.register(sub)
    init.register(sub)
    list_cmd.register(sub)
    explain.register(sub)
    journal.register(sub)
    review.register(sub)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
