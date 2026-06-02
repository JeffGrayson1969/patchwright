# Contributing to PatchWright

Thanks for taking the time to contribute. This guide covers what you need to know to get a PR landed.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). By participating you agree to abide by its terms. Reports to `conduct@patchwright.dev`.

## Quick start

Requirements: Python 3.12+, `uv` (install via `brew install uv` or [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)).

```bash
git clone https://github.com/JeffGrayson1969/patchwright
cd patchwright
uv sync                      # creates .venv, installs runtime + dev deps
uv run pre-commit install    # one-time hook setup
uv run pytest -q             # run the test suite
uv run patchwright hello     # run the P0 demo
```

## Sign-off (DCO — required)

We require the [Developer Certificate of Origin](https://developercertificate.org/) on every commit. There is **no CLA**.

Sign off with `-s`:

```bash
git commit -s -m "feat(core): add journal verify command"
```

This appends `Signed-off-by: Your Name <your@email>`. CI rejects PRs missing DCO.

## Commit messages — Conventional Commits

Format: `type(scope): subject` — see [conventionalcommits.org](https://www.conventionalcommits.org/).

Types we use: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`.

Reference PRD IDs in the body when applicable:

```
feat(journal): add Merkle-chain verify

Implements FR-PV-3 (content-addressed, chained journal entries) and
provides the foundation for FR-PV-4 (signed entries) in P2.
```

## Branches and PRs

- Branch from `main`. Name: `feat/...`, `fix/...`, `docs/...`.
- Open a draft PR early. CI runs on draft.
- PRs need: green CI, DCO sign-off, one approving review for non-trivial changes.
- Squash-merge is the default. The squash commit message should be Conventional Commits format and DCO-signed.
- No force-push to `main`, ever.

## What CI checks

- `ruff check` and `ruff format --check`
- `mypy` (strict on `src/patchwright/core/`)
- `pytest -q --cov` (coverage target: ≥85% on `patchwright.core.*`)
- DCO sign-off on every commit
- macOS + Linux × Python 3.12 + 3.13

## Tests

- Add a test for every behavior change. Bug fix → regression test that fails without the fix.
- Tests live in `tests/` mirroring `src/patchwright/` layout.
- Integration tests that hit the filesystem are fine — use `tmp_path` fixture.
- Don't mock what you can run directly. The journal/FSM is fast; just run it.

## Architectural commitments

Before proposing a non-trivial change, read [`CLAUDE.md`](CLAUDE.md) "Non-negotiable architectural commitments" and [`PRD.md`](PRD.md) §10.1. Examples of changes that need a PRD revision before code review:

- Anything that would let an LLM directly mutate files (two-phase patch rule)
- Anything that introduces a parallel state store outside the journal
- Any auto-merge / auto-file / auto-publish path
- Adding a learned classifier for triage

## Reporting bugs

- **Security bugs:** see [`SECURITY.md`](SECURITY.md) — **do not** open a public issue.
- **Functional bugs:** open a GitHub issue using the Bug template; include `patchwright --version`, OS, repro steps, and the relevant journal entries.

## Good first issues

Issues tagged `good-first-issue` are sized for a first contribution. If you're new, comment to claim one before starting work.

## Questions

Open a discussion in the GitHub Discussions tab, or email `hello@patchwright.dev`.
