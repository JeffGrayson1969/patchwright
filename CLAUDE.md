# CLAUDE.md — PatchWright

Working notes for Claude Code sessions in this repo. Source of truth for *what* we're building is `PRD.md`; this file is the *how-to-collaborate* layer.

## What this project is

PatchWright is an open-source, model-agnostic agent runtime that turns a vulnerability finding into a reviewed patch and a published advisory, with humans in the loop at every decision that matters. Apache-2.0 core; commercial "Shield" tier for high-assurance runtime guarantees.

Read `PRD.md` for vision, personas, functional requirements (FR-*), non-functional requirements (NFR-*), threat model (T1–T10), architecture, and roadmap. Don't restate it here — link to section numbers.

## Current status

- **Phase:** P1 — Triage + Patch MVP (in flight; per `phase1-work-plan.md`)
- **Version:** PRD 0.2-public
- **Owner:** Jeff Grayson
- **Repo:** https://github.com/JeffGrayson1969/patchwright
- **License:** Apache-2.0 (core); commercial license planned for Shield tier

### P0 — closed on the code side
- [x] Repo, license, code of conduct, security.txt
- [x] Orchestrator skeleton + journal + FSM + artifact store
- [x] Two trivial agents (`noop_triage`, `noop_closer`) — retained as reference impls
- [x] Hello-world case completes end-to-end (`uv run patchwright hello`)
- [ ] 1 OSS maintainer design partner identified ← human task; runs in parallel with P1 waves

### P1 milestone checklist (PRD §13 — FR-IN-1/2/5, FR-TR-1/2/3, FR-RP-1/2, FR-PT-1/2/3, FR-HR-1/2/3, FR-PV-1/2/3, FR-CF-1/2, NFR-S-1/4/7/8/10)

Wave A — Foundation (done):
- [x] **M1** — `LLMProvider` + Anthropic/OpenAI-compat providers + real `triage` agent (AEG-367)
- [x] **M2-codemod** — LibCST deterministic patch apply + 3 CWE fixtures (AEG-368)
- [x] **M3-shim** — `SandboxRunner` Protocol + Docker dev backend (AEG-369)
- [x] **M4** — Human review CLI (`list`, `review`, `explain`) (AEG-370)
- [x] **M5-config** — `patchwright.yaml` + provider factory + `embargo_mode: strict` gate (AEG-371)

Wave B — Integration (in flight):
- [x] **M2-plan** — `patch_plan` agent (LLM Phase A, FR-PT-1)
- [x] **M2.5** — `cross_checker` agent (T9 mitigation; gates `PATCH_PROPOSED → PATCH_APPLIED`)
- [x] **M2-pr** — `RepoAdapter` Protocol + `gh`-backed `GitHubRepoAdapter` + `patch_apply` agent + `TransitionEffects` registry + end-to-end test (AEG-374 — FR-PT-3)
- [x] **M6** — Intake adapters (AEG-377):
  - [x] M6.1 — `IntakeAdapter` Protocol + `Report`/`ReporterIdentity` types + T10 helper (AEG-442)
  - [x] M6.2 — generic OSV-JSON `JSONIntakeAdapter` (AEG-443)
  - [x] M6.3 — `GHSAIntakeAdapter` + `ingest()` entry point + E2E test (AEG-444)
- [ ] **M3-hard** — gVisor + network-deny + RO FS hardened sandbox + `reproduce` agent (AEG-375 — FR-RP-1/2, T6):
  - [ ] M3-hard.1 — `sandboxes/gvisor.py` (GVisorSandbox + structural tests) (AEG-461)
  - [ ] M3-hard.2 — `agents/reproduce.py` (TRIAGED → REPRODUCED | NOT_REPRODUCIBLE | REJECTED) (AEG-462)
  - [ ] M3-hard.3 — real CVE fixture + e2e + T6 negative tests (AEG-463)
- [ ] **M3-encrypt** — Embargoed-case journal encryption via age/sops (AEG-376 — T4)

Wave C — Productionization (not started):
- [ ] **M5-plugin + M8** — Plugin SDK + SLSA L3 + cosign release pipeline (AEG-378)
- [ ] **M7** — MCP server (stdio) with 8 tools per PRD §A.1 (AEG-379)

Wave D — Pilot:
- [ ] **M9** — Design-partner pilot + `0.1.0` release (AEG-380); blocked on design-partner identification

### P1 exit gate (PRD §13)

*"Design partner ships a real patch from a real report through PatchWright."* Plus: `patchwright serve --mcp` drives a case via the 8 MCP tools; `cosign verify` and `slsa-verifier` pass on `0.1.0`.

### FSM as of today (`core/fsm.py`)

```
INTAKE          -> TRIAGED | REJECTED
TRIAGED         -> REPRODUCED | NOT_REPRODUCIBLE | REJECTED
REPRODUCED      -> PATCH_PROPOSED | REJECTED
PATCH_PROPOSED  -> PATCH_APPLIED | REJECTED         (gated by cross_checker)
PATCH_APPLIED   -> AWAITING_REVIEW | REJECTED       (PR opened via TransitionEffect)
AWAITING_REVIEW -> DONE | REJECTED
```

## Non-negotiable architectural commitments

These come from PRD §10.1 and must not be violated without an explicit PRD revision:

1. **Finite-state-machine orchestrator** — every case is in exactly one named state; transitions are atomic and journaled.
2. **Append-only JSONL journal is the primary state store** — no separate "engine state"; rebuild by replay.
3. **Sub-agents rehydrate from disk artifacts** every invocation — no long-lived in-memory agent context.
4. **Two-phase patch generation** — Phase A LLM emits a natural-language *plan*; Phase B deterministic codemod/AST tool applies it. **The LLM never writes file mutations directly.** This is the primary T1 (malicious patch) mitigation.
5. **Retrieval-augmented semantic dedup** for triage — not a learned classifier.
6. **Cross-checker agents** for critical outputs — second agent on a *different* provider re-evaluates before any state transition that affects the outside world (T9 mitigation).
7. **All adapter boundaries are plugins** — including the model-provider call (this is the model-agnosticism layer).
8. **No auto-merge, no auto-file in v1.** Human approval required at every state transition by default.

If a suggested change would weaken any of these, push back and ask before implementing.

## Hard "do not"s

- Don't introduce a learned classifier for triage (use rule-based + RAG).
- Don't let the LLM directly mutate files — always go through the codemod layer.
- Don't add auto-merge / auto-file / auto-publish paths in v1.
- Don't store secrets in plain config — keychain or Vault adapter only (NFR-S-10).
- Don't add telemetry by default — opt-in only, aggregate counts only (NFR-S-12).
- Don't add a CLA — DCO only (PRD §12.4).
- Don't duplicate OSV/CSAF/OpenVEX schemas — we consume them, not reinvent them (PRD §3.2).
- Don't write multi-paragraph docstrings or comment blocks — keep code comments to a single line, only where the *why* is non-obvious.

## Tech-stack defaults (until contradicted)

- **Language:** Python (per PRD §10.2 module paths `patchwright.core.*`)
- **Install:** `pipx install patchwright` or single binary via PyInstaller (NFR-T-2)
- **Storage:** SQLite for single-maintainer; Postgres for team/coordinator (NFR-T-3)
- **Sandbox:** gVisor or Firecracker (NFR-S-1)
- **Codemod tooling:** LibCST (Python), Comby (multi-lang), ts-morph (JS/TS) (PRD §10.2)
- **Validation:** Pydantic / JSON Schema for all agent outputs (T2 mitigation)
- **Journal:** content-addressed SHA-256, Merkle-chained, JSONL (FR-PV-1/3)

## Module layout (target, per PRD §10.2)

```
patchwright/
  core/orchestrator.py        # FSM driver, journal manager, plugin loader
  agents/                     # triage, reproduce, patch_plan, patch_apply, test, advisory, disclose, cross_checker
  tools/                      # git, codemod, sandbox runners, osv-scanner wrappers
  adapters/                   # intake, scanner, advisory, notification, review
  models/                     # AnthropicProvider, OpenAIProvider, MCPSamplingProvider (PRD §A.3)
  storage/                    # sqlite/postgres + content-addressed object store + JSONL journal
  cli/                        # init, ingest, list, review, explain, journal, serve
```

The MCP server is the **primary** integration surface (PRD §A.1): `patchwright serve --mcp` exposes 8 tools. CLI is secondary.

## How to track progress in this repo

- **Roadmap state:** the P0 checklist above is canonical for *this* phase. When P0 closes, replace with a P1 checklist taken from PRD §13.
- **Per-feature work:** reference the FR-* ID from PRD §6 in commit messages and PR titles (e.g. `FR-PT-1: scaffold two-phase patch generator`).
- **Threat-model coverage:** when you add code that mitigates a Tn threat, note it in the commit (`T2 mitigation: wrap user-supplied text with injection delimiters`).
- **Status questions:** ask "where are we against the P0 checklist?" — answerable from `git log` + this file. If the answer requires more than that, the checklist is stale; fix the checklist.

## Working conventions for Claude sessions

- This repo is **public**. Don't write anything to disk that references held-private material (PRD §11 patent rationale, named partners, internal estimates).
- Default to small, reviewable commits. Reference the FR-* / NFR-* / Tn ID being addressed.
- Don't push without an explicit "push" from Jeff.
- Don't add features beyond the immediate task — PRD §3.2 non-goals exist for a reason.
- When unsure whether something belongs in OSS core vs. Shield, default to OSS core (PRD §12.3: "the OSS core is feature-complete for self-hosting").

## Glossary shortcuts

Full glossary in PRD §17. The terms that come up most:

- **CSAF** — advisory format we emit, never invent
- **OpenVEX** — exploitability statement format
- **OSV** — vulnerability schema we normalize *to* on intake
- **SLSA** — supply-chain provenance level (we target L3)
- **TEE** — Trusted Execution Environment (Shield tier)
- **VINCE** — CERT/CC coordination platform (intake adapter, Phase 2)

## When to update this file

Update when:
- Phase advances (P0 → P1, etc.) — swap the checklist
- A non-negotiable architectural commitment changes in the PRD
- A new hard "do not" emerges from a real incident or review
- The module layout drifts from what's documented above

Don't update for:
- Routine task completion (use commits)
- In-progress thoughts (use the PR description)
- Things derivable from `git log` or current code
