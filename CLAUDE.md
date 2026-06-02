# CLAUDE.md — PatchWright

Working notes for Claude Code sessions in this repo. Source of truth for *what* we're building is `PRD.md`; this file is the *how-to-collaborate* layer.

## What this project is

PatchWright is an open-source, model-agnostic agent runtime that turns a vulnerability finding into a reviewed patch and a published advisory, with humans in the loop at every decision that matters. Apache-2.0 core; commercial "Shield" tier for high-assurance runtime guarantees.

Read `PRD.md` for vision, personas, functional requirements (FR-*), non-functional requirements (NFR-*), threat model (T1–T10), architecture, and roadmap. Don't restate it here — link to section numbers.

## Current status

- **Phase:** P0 — Spike (pre-design, public review)
- **Version:** PRD 0.2-public
- **Owner:** Jeff Grayson
- **Repo:** https://github.com/JeffGrayson1969/patchwright
- **License:** Apache-2.0 (core); commercial license planned for Shield tier
- **What exists today:** PRD, README, license, this file. No code yet.

### P0 exit criteria (PRD §13)
- [x] Repo, license, code of conduct, security.txt
- [x] Orchestrator skeleton
- [x] One trivial agent (two, actually — `noop_triage` + `noop_closer`)
- [ ] 1 OSS maintainer design partner identified ← human task
- [x] Hello-world case completes end-to-end (`uv run patchwright hello`)

When the design-partner item closes, swap this checklist for the P1 checklist (PRD §13 → FR-IN-1/2, FR-TR-1/2/3, FR-RP-1/2, FR-PT-1/2/3, FR-HR-1/2/3, FR-PV-1/2/3, FR-CF-1/2, NFR-S-1/4/7/8/10).

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
