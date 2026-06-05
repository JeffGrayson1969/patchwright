"""Tool wrappers: codemod runners, sandbox launchers, scanner shims, etc.

Tools are deterministic. They do NOT call LLMs (that's agents). They are the
"deterministic Phase B" half of PRD §10.1 commitment 4 (two-phase patch
generation).
"""
