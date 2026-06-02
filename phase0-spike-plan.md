# PatchWright P0 — Spike Implementation Plan

> **Status:** Code deliverables (A–F below) complete and merged on `main`. The remaining P0 exit-criterion — *identify one OSS maintainer design partner* — is a human task tracked outside this document. This file is preserved as the historical spike plan.

## Context

PatchWright is in Phase 0 ("Spike") per `PRD.md` §13. The repo currently has only documentation (PRD, README, CLAUDE.md, LICENSE, .gitignore). The README publicly commits to artifacts that don't exist yet (CODE_OF_CONDUCT, SECURITY.md, security.txt, CONTRIBUTING, pyproject.toml, `phase0-spike-plan.md`). This plan closes the P0 exit-criteria checklist in CLAUDE.md:

- Repo, license, code of conduct, security.txt ← partial; license done
- Orchestrator skeleton ← missing
- One trivial agent ← missing
- Hello-world case completes end-to-end ← missing
- 1 OSS maintainer design partner identified ← human task, **not in this plan**

The deliberate principle: **build the minimum that proves the load-bearing architectural commitments** (FSM, append-only journal as state store, agent-as-pure-function, plugin seam) so P1 can extend rather than refactor.

## Tech-stack choices (confirmed)

- Python 3.12+ (README badge)
- **uv** for venv/lockfile/build
- src-layout (`src/patchwright/...`)
- Pydantic for all I/O
- pytest for tests
- GitHub Actions for CI

## Deliverables

### A. Governance & repo hygiene (Conventional Commits, DCO)

- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1 verbatim, contact = `conduct@patchwright.dev`
- `CONTRIBUTING.md` — DCO (`git commit -s`), Conventional Commits, `uv` dev setup, test commands, branch model, good-first-issue tag explanation
- `SECURITY.md` — disclosure policy, embargo defaults (90d standard / 14d critical), PGP-or-Signal contact, "do not file public issues for vulns"
- `.well-known/security.txt` — RFC 9116 format, `Contact:`, `Expires:`, `Preferred-Languages:`, `Canonical:`
- `.github/ISSUE_TEMPLATE/` — bug, feature, security (security points to SECURITY.md)
- `.github/PULL_REQUEST_TEMPLATE.md` — DCO checkbox, FR-/NFR-/Tn reference, test plan
- `.github/dependabot.yml` — pip + github-actions ecosystems, weekly
- `phase0-spike-plan.md` — committed copy of *this* plan (README references it)

### B. Python project skeleton

- `pyproject.toml` (PEP 621):
  - `[project]` — name, version `0.0.0`, Python `>=3.12`, classifiers, license = Apache-2.0
  - `[project.scripts] patchwright = "patchwright.cli.__main__:main"`
  - `[project.entry-points."patchwright.plugins.agents"]` — `noop_triage`, `noop_closer`
  - Runtime deps: `pydantic>=2.7`
  - Dev deps (`[dependency-groups]` per PEP 735): `pytest`, `pytest-cov`, `ruff`, `mypy`, `pre-commit`
- `.python-version` — `3.12`
- `uv.lock` — committed
- `ruff.toml` — line length, lint rules (E, F, I, B, UP, RUF), formatter on
- `mypy.ini` — `strict = True` for `src/patchwright/core/`
- `.pre-commit-config.yaml` — ruff format + check, mypy, DCO sign-off check
- `.gitattributes` — text/binary defaults, LF line endings

### C. CI (GitHub Actions)

- `.github/workflows/ci.yml` — matrix on `ubuntu-latest` + `macos-latest`, Python 3.12 + 3.13; steps: `uv sync`, `ruff check`, `ruff format --check`, `mypy`, `pytest -q --cov`
- `.github/workflows/dco.yml` — DCO check on PRs
- (Defer SLSA L3 provenance + cosign signing to P1 release step.)

### D. Core code — `src/patchwright/`

Module map (P0 only):

```
src/patchwright/
  __init__.py                 # __version__
  core/
    __init__.py
    models.py                 # Pydantic: Case, JournalEntry, Artifact, Transition, AgentResult
    fsm.py                    # state graph + is_legal()
    hashing.py                # canonical_json() + sha256_b16
    artifacts.py              # content-addressed blob store
    journal.py                # append-only JSONL, Merkle chain, torn-tail recovery
    protocols.py              # Agent Protocol (PEP 544), ReadOnlyArtifactStore
    registry.py               # entry-points loader + default_registry()
    orchestrator.py           # drive() loop
    errors.py
  agents/
    __init__.py
    noop_triage.py            # INTAKE -> TRIAGED, emits triage_packet artifact
    noop_closer.py            # TRIAGED -> DONE, no artifacts
  cli/
    __init__.py
    __main__.py               # argparse dispatch
    hello.py                  # `patchwright hello [--root DIR]`
  fixtures/
    hello_report.json         # canonical OSV-ish fake report
```

**Hash & chain contract** (from the Plan-agent design):

- `content_hash = "sha256:" + sha256(canonical_json({seq, case_id, ts, kind, author, prev_hash, payload}))`
- Canonical JSON: UTF-8, `sort_keys=True`, `separators=(",", ":")`, integers as ints
- `signature` field reserved (nullable) in P0 schema — populated in P2 (FR-PV-4)
- Genesis `prev_hash = "sha256:" + "0"*64`
- Per-case journal directory: `<root>/journal/<case_id>/journal.jsonl`
- Per-case artifacts: `<root>/artifacts/<sha>.bin` (global, content-addressed → safe to share)

**Atomic write order** (per transition):

1. Each new artifact: write `.tmp`, fsync, rename to final, fsync parent dir
2. Build `JournalEntry`, compute `content_hash`, serialize line
3. Append to journal: `fsync(fd)` then `fsync(parent_dir)`

**Recovery:** torn-tail line on replay → truncate single bad trailing line, log warning, continue. Orphan artifacts (written, no journal ref) → ignored at replay, cleaned by future `journal verify` (not in P0).

**Agent contract** (`protocols.py`):

```python
class AgentResult(BaseModel):
    transition: Transition
    new_artifacts: list[tuple[bytes, str]]   # (raw_bytes, kind)
    reason: str

class Agent(Protocol):
    name: str
    handles_state: str
    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult: ...
```

Agents never touch disk. The orchestrator owns all writes and all journal appends. This is the seam where Phase B (deterministic codemod) plugs in for P1's two-phase patch generation.

**Orchestrator** (`orchestrator.py`):

```python
def drive(case_id, registry, root) -> Case:
    case = replay(journal_for(case_id, root), artifact_store(root))
    while case.state not in TERMINAL_STATES:
        agent = registry.agent_for_state(case.state)
        if agent is None: break               # human-required — no auto-action
        result = agent(case, store.read_only())
        validate_transition_legal(result.transition, case.state)
        refs = [store.put(b, k) for (b, k) in result.new_artifacts]
        journal.append_transition(case, result.transition, refs, author=f"agent:{agent.name}")
        case = replay(journal, store)         # runtime invariant: replay = state
    return case
```

Re-replay after every transition makes NFR-R-1 (idempotent agent actions) and NFR-R-2 (crash-replay correctness) executable invariants, not just test assertions.

### E. Tests — `tests/`

Five P0 tests, mapping to the architectural commitments:

1. `test_journal_content_addressing.py` — same canonical payload → identical `content_hash`; one-byte payload change → different hash; signature field does not affect hash.
2. `test_journal_replay_idempotent.py` — `drive()` to DONE, then `replay()` again → deep-equal Case; second `drive()` → no new entries.
3. `test_journal_crash_safe_append.py` — write valid journal, append half-line mid-JSON → `replay()` recovers cleanly; next `drive()` appends with correct `prev_hash`.
4. `test_orchestrator_illegal_transition.py` — bad agent proposes `INTAKE → DONE`; `drive()` raises `IllegalTransition`; case state still `INTAKE`; journal has rejection entry.
5. `test_hello_end_to_end.py` — invoke CLI; assert state=DONE, expected `kind` sequence, all `content_hash` verify, chain unbroken from genesis.

Coverage target P0: ≥85% on `patchwright.core.*`.

### F. README touch-ups

- Fix or stub broken links to `phase1-work-plan.md` and `standards-deep-read.md` (not in P0 scope; either stub to `TBD` or remove links).

## Implementation order (suggested commit sequence)

1. `chore: scaffold pyproject + uv + ruff + mypy + pre-commit` (B + part of C)
2. `chore: governance docs (CoC, CONTRIBUTING, SECURITY, security.txt, issue/PR templates)` (A)
3. `feat(core): models, fsm, hashing, artifacts` (D, partial)
4. `feat(core): append-only journal with Merkle chain` (D + test #1, #3)
5. `feat(core): orchestrator drive loop + agent protocol + registry` (D + test #4)
6. `feat(agents): noop_triage and noop_closer` (D)
7. `feat(cli): patchwright hello demo command` (D + test #5)
8. `test: replay idempotence end-to-end` (test #2)
9. `ci: github actions matrix + DCO check + dependabot` (C)
10. `docs: phase0-spike-plan.md committed; README link fixes` (A + F)

Each commit references the FR-/NFR- IDs it satisfies. Conventional Commits + DCO sign-off enforced from commit 1.

## Verification (how we know P0 is done)

End-to-end demo (matches CLAUDE.md exit criterion):

```bash
uv sync
uv run pytest -q                              # all green, coverage ≥85% on core
uv run patchwright hello --root /tmp/pw-demo  # prints journal, terminates DONE
uv run patchwright hello --root /tmp/pw-demo  # second run: "replay produced identical state, last_hash unchanged"
```

Plus:
- `ruff check`, `ruff format --check`, `mypy src/patchwright/core` clean
- GitHub Actions green on PR for both Linux + macOS, Python 3.12 + 3.13
- DCO check green
- All README links resolve

## Explicitly out of scope for P0 (defer to P1+)

- Real LLM providers (`AnthropicProvider`, `OpenAIProvider`, `MCPSamplingProvider`) — P1
- Sandbox runners (gVisor / Firecracker) — P1
- Real adapters (intake, scanner, ticket, repo) — P1+
- Two-phase patch *implementation* (only the *seam* is in P0) — P1
- Journal signing (FR-PV-4) — P2; field is reserved in schema
- CSAF / OpenVEX / CVE Services / VINCE — P2
- SQLite or Postgres metadata store — P1 (per PRD §10.2; deliberately not in P0)
- MCP server (`patchwright serve --mcp`) — P1
- SLSA L3 release provenance + cosign — P1 release step
- Web review UI, Slack/Linear adapters — P3
- Design-partner identification — non-code, human task

## Open question for review

The 5 tests cover the architectural invariants but not the full FSM. Want me to add `test_fsm_all_legal_paths.py` enumerating every edge in the (currently 4-edge) state graph? Cheap to add now; protects against accidental graph edits in P1.
