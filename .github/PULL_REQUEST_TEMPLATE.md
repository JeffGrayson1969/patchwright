<!--
Thanks for your contribution! Please complete the checklist below.
For security-sensitive changes, see SECURITY.md.
-->

## Summary

<!-- One or two sentences on what changes and why. -->

## PRD reference

<!-- FR-* / NFR-* / Tn IDs this addresses, or "no PRD reference" with rationale. -->

## Test plan

- [ ] Unit tests added/updated
- [ ] Manual verification (commands run, expected vs. actual)
- [ ] `uv run pytest -q` passes locally
- [ ] `uv run ruff check && uv run ruff format --check` pass locally
- [ ] `uv run mypy` passes locally

## Checklist

- [ ] Commits are Conventional Commits format
- [ ] Every commit is DCO-signed (`git commit -s`)
- [ ] CLAUDE.md / PRD.md updated if architecture or commitments changed
- [ ] No secrets, fixtures, or large binaries added
- [ ] If this changes a non-negotiable commitment (CLAUDE.md), the PRD revision is linked

## Reviewer checklist (code owners)

<!--
For the approving CODEOWNER. PatchWright is a high-value supply-chain target —
review as if the author is untrusted (threat model anti-persona "Sam", PRD §9).
Read the diff, not just the green checks.
-->

- [ ] Diff matches the description; no unexplained, unrelated, or obfuscated changes
- [ ] Two-phase patch rule intact — the LLM never writes file mutations directly; only the codemod applies changes (CLAUDE.md #4)
- [ ] No new auto-merge / auto-file / auto-publish path (CLAUDE.md #8)
- [ ] No secrets/tokens added; secrets stay in keychain/config, never in the journal (NFR-S-10)
- [ ] New/changed dependencies are necessary, reputable, and lockfile-pinned; Dependency Review is green
- [ ] No new outbound network, subprocess, `eval`/`exec`, or deserialization surface without justification
- [ ] Embargo/T4 intact — nothing routes embargoed data to a non-local LLM or a plaintext journal
- [ ] No non-negotiable commitment weakened (CLAUDE.md) without a linked PRD revision
- [ ] CodeQL / Dependency Review / tests / DCO all green **and** the diff was read
