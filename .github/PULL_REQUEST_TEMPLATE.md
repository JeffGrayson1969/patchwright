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
