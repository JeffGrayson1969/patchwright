# PatchWright P1 — Triage + Patch MVP Work Plan

> **Status:** Active. Phase 0 (Spike) is closed on the code side; the design-partner item runs in parallel with Wave A below.

## Context

Phase 0 proved the load-bearing architecture: a finite-state-machine orchestrator with replay-after-every-transition, an append-only Merkle-chained JSONL journal as the primary state store, a stateless Agent Protocol where agents return bytes and the orchestrator owns I/O, and a plugin registry. What P0 *intentionally* didn't ship: any real LLM, any sandbox, any real adapter, any patch generation.

P1's purpose is to turn that skeleton into a usable triage-and-patch MVP for the **Olivia persona** (PRD §4.1 — solo OSS maintainer drowning in AI-generated reports). Exit gate for P1 (PRD §13): *"Design partner ships a real patch from a real report through PatchWright."*

This plan deliberately commits to the realistic timeline. **~14 weeks for one engineer**, organized into four waves with explicit parallelism. A compressed ~10-week variant is documented at the bottom for the case where scope must shrink.

## Baseline (what P0 shipped)

Already in `main` — these are the seams P1 extends, not refactors:

- `core/orchestrator.py` — `drive()` loop, replay-after-every-transition (NFR-R-1/2 as runtime invariants)
- `core/journal.py` — Merkle-chained JSONL, torn-tail recovery, content-addressing (FR-PV-1/2/3)
- `core/artifacts.py` — content-addressed blob store + `ReadOnlyArtifactStore` view handed to agents
- `core/protocols.py` — `Agent` Protocol (PEP 544); agents return `AgentResult` bytes, never touch disk
- `core/fsm.py` — explicit state graph (currently 4 edges; P1 extends it)
- `core/registry.py` — entry-points loader + explicit `default_registry()`
- `core/models.py` — Pydantic `Case`, `JournalEntry`, `Artifact`, `Transition`, `AgentResult`; `signature` field reserved nullable for FR-PV-4
- `agents/noop_*.py` — kept as reference impls and for tests; replaced in default flow by real agents

## Tech additions for P1

- **`anthropic`** SDK (Claude provider)
- **`openai`** SDK (OpenAI + base_url for Groq/OpenRouter/Together/Ollama/vLLM — single SDK, many backends per PRD A.3)
- **`mcp`** Python SDK (server in M7; sampling client in M1)
- **`libcst`** (Python AST rewriting — patch_apply for Python)
- **`keyring`** (OS keychain — NFR-S-10)
- **`age`** / **`sops`** binary dep for journal encryption (T4)
- **`PyGithub`** or **`gh`** subprocess (GitHub RepoAdapter)
- **`docker`** Python SDK (M3 sandbox dev backend)
- **`pyyaml`** (`patchwright.yaml`)
- **`cosign`** binary (M8 release signing)

State-graph additions (`core/fsm.py`):
```
INTAKE     -> TRIAGED | REJECTED
TRIAGED    -> REPRODUCED | NOT_REPRODUCIBLE | REJECTED
REPRODUCED -> PATCH_PROPOSED | REJECTED
PATCH_PROPOSED -> PATCH_APPLIED | REJECTED
PATCH_APPLIED  -> AWAITING_REVIEW
AWAITING_REVIEW -> DONE | REJECTED
```

---

## Wave A — Foundation (~3 weeks; parallel tracks)

Five tracks ship in parallel. None block each other. Land them before Wave B starts so Wave B has all the contracts it needs.

### M1 · LLMProvider + first real triage agent
- `core/llm.py` — `LLMProvider` PEP 544 Protocol: `complete(messages, response_schema) -> str | BaseModel`
- `providers/anthropic.py` — `AnthropicProvider` via official SDK
- `providers/openai_compat.py` — `OpenAIProvider` with configurable `base_url` (covers Ollama, vLLM, Groq, OpenRouter)
- `providers/mcp_sampling.py` — stub (matures in M7)
- `core/secrets.py` — `keyring`-backed key lookup, env-var fallback (NFR-S-10)
- `agents/triage.py` — replaces `noop_triage`; calls LLM with delimiter-wrapped report (T2 mitigation), returns Pydantic-validated `TriagePacket`
- `agents/cross_checker.py` (stub) — fully wired in M2.5
- **NFR-S-4 horizontal**: extend `ArtifactStore` with `verify_signature(sha) -> bool`; gated by `--no-verify-signatures` dev flag. Prompts and codemod rules carry detached signatures from this point forward.
- **Maps:** FR-TR-2, FR-TR-3; mitigates T2 (prompt injection), T5 (model artifact tampering); enables R1 (source code stays local via OpenAI base_url)
- **Exit:** real triage of a fixture report against Claude returns a valid `TriagePacket`; the same flow against `ollama` (via base_url) returns a valid `TriagePacket`; secrets never appear in journal entries (regression test).

### M2-codemod · Deterministic patch application (no LLM)
- `tools/codemod_python.py` — LibCST wrapper that accepts a typed `PatchPlan` and emits a unified diff + modified files
- `models/patch_plan.py` — Pydantic `PatchPlan` schema (operations: `replace_function_body`, `insert_import`, `wrap_call_with_validator`, `add_test_case`, etc.). Discriminated union for safety.
- `tools/test_gen_python.py` — generates a regression test file from a `PatchPlan.test_spec`
- Fixture corpus in `tests/fixtures/patch_corpus/` — at minimum 3 distinct Python CWE shapes (path traversal, SQL injection, deserialization) with: vulnerable code + hand-authored `PatchPlan` + expected diff
- **Maps:** FR-PT-1 Phase B (the deterministic half); foundation for T1 mitigation
- **Exit:** for each fixture, `apply(plan, repo) -> diff` produces the expected diff byte-for-byte; the generated test fails on the vulnerable code and passes after the patch is applied.

### M3-shim · Sandbox Protocol + Docker dev backend
- `core/sandbox.py` — `SandboxRunner` Protocol: `run(image, cmd, mounts, timeout) -> RunResult`
- `sandboxes/docker.py` — Docker backend (works on macOS dev + Linux dev)
- `core/orchestrator.py` extension: the `repro` and `patch_test` agents call sandbox via this Protocol
- **Maps:** seam for FR-RP-1/2 (hardened backend in Wave B)
- **Exit:** `SandboxRunner.run(...)` executes a fixture PoC inside Docker; outcome (exit code, stdout, stderr, env snapshot) recorded as journal artifact; no network access by default (Docker `--network=none`).

### M4 · Human review CLI
- `cli/list.py` — `patchwright list` shows open cases, state, age, last journal entry
- `cli/review.py` — `patchwright review <case-id>` opens an editor (`$EDITOR`) with the markdown evidence packet; on save, parses approve/edit/reject/fork verbs; appends `human_decision` journal entry
- `cli/explain.py` — `patchwright explain <case-id>` renders the case decision tree
- `core/evidence.py` — packs case state + artifacts + reasoning trace into reviewable markdown
- **Maps:** FR-HR-1, FR-HR-2, FR-HR-3
- **Exit:** Olivia-persona can run `patchwright review <id>`, approve, and the journal records the decision with her identity (from `git config user.email` or `patchwright.yaml`).

### M5-config · `patchwright.yaml` loader (no plugin SDK yet)
- `core/config.py` — Pydantic `PatchwrightConfig` with defaults from PRD §6.8; loaded via `patchwright init`
- Per-project conventions section (code style, test command, branch model) consumed by M2 patch generation
- `embargo_mode: strict` flag — gates M1's LLMProvider selection (T4 + R2 mitigation)
- **Maps:** FR-CF-1; plugin SDK (FR-CF-2) deferred to Wave C with M8 (same signing toolchain)
- **Exit:** `patchwright init` produces a working config for a typical GitHub-hosted Python repo; `embargo_mode: strict` hard-fails any non-local LLM call (test).

---

## Wave B — Integration (~5 weeks)

The "real" agents go in. T1 mitigation lands here.

### M2-plan · `patch_plan` agent + LLM-emitted `PatchPlan`
- `agents/patch_plan.py` — LLM call with structured output (`response_schema=PatchPlan`) over (vulnerability description + repo context + fix conventions)
- Repo-context retrieval: AST snippet around the suspect symbol via LibCST, plus per-project conventions from `patchwright.yaml`
- The agent emits the `PatchPlan` artifact bytes; orchestrator persists it; M2-codemod (Wave A) is the apply step
- **Maps:** FR-PT-1 Phase A
- **Exit:** for each of the 3 CWE fixtures, the LLM produces a `PatchPlan` that, when fed to M2-codemod, yields a passing test.

### M2.5 · `cross_checker` agent (T9 mitigation)
- `agents/cross_checker.py` — re-evaluates the primary `patch_plan` agent's output using a *different* LLMProvider; refuses transition if it can't reconstruct the same intent from the original report
- Forces the FSM transition `PATCH_PROPOSED -> PATCH_APPLIED` to require cross-checker pass
- Single-provider mode (OSS): same provider, different prompt and instructions; full multi-provider consensus is Shield-tier (PRD §12.2)
- **Maps:** T9 mitigation; foundation for Shield's consensus agent

### M2-pr · `RepoAdapter` (GitHub) + feature-branch PR
- `core/repo.py` — `RepoAdapter` PEP 544 Protocol: `create_branch`, `commit_files`, `open_pr_draft`
- `adapters/repo_github.py` — backed by `gh` subprocess or PyGithub (decide based on auth ergonomics)
- `agents/patch_apply.py` — orchestrates codemod apply + test run + PR creation
- **Maps:** FR-PT-3
- **Exit:** end-to-end run on a fixture creates a draft PR with patch + test in a feature branch.

### M3-hard · gVisor + network-deny + read-only FS
- `sandboxes/gvisor.py` — Linux-only hardened backend
- Network policy default-deny; per-case allowlist (NFR-S-2)
- Filesystem default-read-only outside `/case/scratch` (NFR-S-3)
- `agents/reproduce.py` — calls hardened sandbox; records outcome + environment snapshot
- **Maps:** FR-RP-1, FR-RP-2; NFR-S-1/2/3; mitigates T6 (sandbox escape)
- **Exit:** PoC repro of a real CVE (e.g. CVE-2024-XXXX from OSV) lands as a `repro_log` artifact in journal; PoC cannot egress network or write outside its scratch dir (negative tests prove it).

### M3-encrypt · Embargoed-case journal encryption (T4)
- Per-case scratch directory encrypted via `sops` / `age`; key rotated per operator
- `journal.append` opt-in encryption for `embargo_mode: strict` cases
- **Maps:** T4 mitigation
- **Exit:** `patchwright journal --case <id>` requires the operator key to decrypt embargoed-case entries.

### M6 · Intake adapters (GHSA + JSON minimal; IMAP + LLMParseAdapter if time)
- `core/intake.py` — `IntakeAdapter` PEP 544 Protocol: `parse(raw) -> Report` (where `Report` normalizes to OSV-Schema)
- `adapters/intake_ghsa.py` — GitHub Security Advisory JSON parser (FR-IN-1, FR-IN-2)
- `adapters/intake_json.py` — generic OSV-shaped JSON intake
- `adapters/intake_imap.py` — *time-permitting* IMAP polling
- `adapters/intake_llm_parse.py` — *time-permitting* LLMProvider-driven text parser (uses M1)
- **T10 mitigation**: reporter identity encrypted at rest; pseudonymous IDs in all public artifacts
- **Maps:** FR-IN-1, FR-IN-2, FR-IN-5; T10
- **Exit:** a GHSA JSON dropped into intake produces a Case in `INTAKE` state with normalized OSV report as the raw artifact; reporter PII never appears in any non-encrypted artifact (regression test).

---

## Wave C — Productionization (~3 weeks)

### M5-plugin + M8 (bundled) · Plugin SDK + supply chain
- `docs/plugins.md` — write your first plugin (intake + agent + provider)
- Plugin signing: unsigned plugins refused by default; operator-configurable trust store (FR-CF-2, NFR-S-8)
- Reproducible build verification (NFR-S-7)
- SLSA L3 provenance via `slsa-github-generator` action
- Release signing via cosign keyless (Sigstore Fulcio)
- `.github/workflows/release.yml` — tag → build wheel + sdist → sign → upload to PyPI + GitHub Releases with attestation
- **Maps:** FR-CF-2, NFR-S-7, NFR-S-8; mitigates T8 (supply-chain attack)
- **Exit:** `0.1.0-rc1` pushed to TestPyPI; `cosign verify-blob` validates the release; `slsa-verifier` passes.

### M7 · MCP server (stdio transport)
- `cli/serve.py` — `patchwright serve --mcp` (stdio)
- 8 MCP tools per PRD A.1: `intake_report`, `triage_case`, `reproduce_poc`, `generate_patch_plan`, `apply_patch`, `draft_advisory` (stub for P2), `get_status`, `explain_case`
- `providers/mcp_sampling.py` matured: in MCP-host mode (Mode B per PRD A.2), the host's LLM drives PatchWright via MCP Sampling
- **Maps:** PRD A.1 primary integration surface
- **Exit:** Claude Code / Cursor / Cline can drive a case INTAKE → AWAITING_REVIEW via the MCP server; streamable HTTP transport deferred to P2.

---

## Wave D — Pilot + first release (~3 weeks)

### M9 · Design-partner pilot + 0.1.0
- Onboard the OSS maintainer design partner (P0 exit-criterion that ran in parallel)
- One real (low-stakes) inbound report driven through the full pipeline by the design partner
- Bug-fix round-trip (budget 2 weeks for iteration)
- Opt-in aggregate telemetry (NFR-S-12) — counts only, no source code, no patch contents
- `0.1.0` release
- **Maps:** PRD §13 P1 exit gate
- **Exit:** *Design partner ships a real patch from a real report through PatchWright.*

---

## Threat-model coverage (PRD §9)

| Threat | Mitigation lands in |
|---|---|
| **T1** — malicious patch via poisoned report | M2-codemod (deterministic apply), M2-plan (LLM only emits plan), M2.5 (cross-checker), M2-pr (mandatory human review) |
| **T2** — prompt injection | M1 (delimiter wrapping, Pydantic-validated outputs) |
| **T3** — AI-slop flood DoS | M1 + M6 (rule-based reporter trust, low-trust requires sandbox repro to advance) |
| **T4** — embargo leak via journal exfil | M5-config (`embargo_mode`), M3-encrypt (age/sops at-rest), R2 |
| **T5** — compromised model artifacts | M1 (signed prompts, startup verification, NFR-S-4) |
| **T6** — sandbox escape during repro | M3-shim (no network default), M3-hard (gVisor + network-deny + RO FS) |
| **T7** — CVE filing impersonation | Not in P1 — CVE filing is P2; current default never auto-files |
| **T8** — supply-chain attack on PatchWright | M8 (SLSA L3, cosign, reproducible builds, pinned deps + osv-scanner) |
| **T9** — model-provider compromise | M2.5 cross-checker |
| **T10** — reporter de-anonymization | M6 (encrypted reporter-ID, pseudonymous public IDs) |

## Enterprise-risk register (PRD §A.5) — P1 touchpoints

| Risk | Mitigation |
|---|---|
| R1 — source code leaving network | M1 OpenAIProvider `base_url` → local Ollama / vLLM |
| R2 — embargoed CVE data exposure | M5 `embargo_mode: strict` (gates M1 + M6) |
| R5 — supply-chain attack | M8 |
| R9 — LLM provider dependency | M1 model-agnostic; M2.5 cross-checker |

## Verification — how we know P1 is done

P1 closes when **all** of the following hold:

1. The Olivia journey from PRD §5.1 runs end-to-end against a *real* OSS repo (test repo or design-partner's repo): inbound report → triage → repro → patch plan → patched + tested → draft PR → human review → approval → journal complete and verifiable.
2. The full 19-test P0 suite still passes plus ≥50 new tests covering M1-M9; ≥85% coverage on `patchwright.core.*` and ≥75% across the whole package.
3. `patchwright serve --mcp` accepts a connection from Claude Code and drives a case via the 8 MCP tools.
4. `cosign verify` and `slsa-verifier` pass on the 0.1.0 release artifact.
5. Design partner has signed off in writing that PatchWright was useful for their workflow.

## Out of scope for P1 (defer to P2+)

- CSAF / OpenVEX / CVE Services / VINCE adapters (P2)
- Multi-repo coordinated patch campaigns (P3)
- Web review UI; Slack/Linear adapters (P3)
- Multi-language patch (only Python in P1; Comby cross-lang in P2)
- Differential repro (P2)
- AI-slop signature detection beyond rule-based trust (P2 — FR-TR-4)
- Patch backports for EOL packages (P3)
- Streamable-HTTP MCP transport (P2)
- Shield-tier features: TEE sandboxes, signed-model runtime attestation, multi-provider consensus, hosted runtime (P4)
- Telemetry beyond opt-in aggregate counts (intentional — NFR-S-12)
- A learned classifier for any triage decision (PRD non-negotiable)

## Open questions for review

1. **GitHub adapter auth** — `gh` subprocess (uses operator's existing auth) or PyGithub (requires PAT in keyring)? Recommendation: `gh` subprocess for v1; switch if SDK ergonomics outweigh the dependency.
2. **Test corpus source** — synthesize our own 3 CWE fixtures, or pull from an existing corpus (e.g. CWE-Bench-Java, Vulcan-Bench)? Recommendation: synthesize for P1 (legal-clear, controlled), import established corpora in P2.
3. **Model defaults** — Anthropic Claude as default provider, with explicit `provider:` in `patchwright.yaml`? Or "no default, must configure"? Recommendation: explicit-configure, no implicit default.
4. **MCP host coverage** — Claude Code first; Cursor, Cline, Aider, Continue, Codex CLI defer to whenever someone files an issue saying their host doesn't work?
5. **Design-partner data handling** — pilot uses real reports against a real repo. Need a data-handling addendum to SECURITY.md (T10 + R1) before pilot begins. *Not in scope for this plan but block M9 on it.*

## Scope-cut variant (if 10 weeks is hard-fixed)

If P1 must close in ~10 weeks rather than ~14, cut in this order (least pain first):

1. **M6** to GHSA + JSON only; drop IMAP and LLMParseAdapter (defer to P2)
2. **M3-encrypt** deferred to P2 (embargo_mode flag still lands; encryption is later)
3. **M8 SLSA L3** deferred; use a manually-signed cosign release for 0.1.0
4. **M7** stdio-only (already the recommendation); skip streamable HTTP entirely in P1
5. **M5-plugin** doc only; no example plugin in P1

These cuts preserve the load-bearing T1 (M2 + M2.5), T2 (M1), T6 (M3-hard), and T8-partial mitigations. They sacrifice T4 (embargo journal encryption) and some breadth in intake/MCP.

---

## Implementation order summary

```
Wave A (parallel):      M1 || M2-codemod || M3-shim || M4 || M5-config
Wave B (mostly serial): M2-plan -> M2.5 -> M2-pr ;  M3-hard ; M3-encrypt ; M6
Wave C (mostly serial): M5-plugin + M8 (bundled) ; M7
Wave D:                 M9
```

Each milestone commits in its own conventional-commits PR, FR-/NFR-/Tn IDs in commit body, DCO-signed, CI green before merge.

---

## Tracking — Linear tickets

Filed in the **PatchWright** Linear project (team Aegisq):

| Milestone | Linear |
|---|---|
| Wave A — M1 LLMProvider + first real triage | [AEG-367](https://linear.app/aegisq/issue/AEG-367) |
| Wave A — M2-codemod Deterministic patch application | [AEG-368](https://linear.app/aegisq/issue/AEG-368) |
| Wave A — M3-shim Sandbox Protocol + Docker | [AEG-369](https://linear.app/aegisq/issue/AEG-369) |
| Wave A — M4 Human review CLI | [AEG-370](https://linear.app/aegisq/issue/AEG-370) |
| Wave A — M5-config `patchwright.yaml` | [AEG-371](https://linear.app/aegisq/issue/AEG-371) |
| Wave B — M2-plan `patch_plan` agent | [AEG-372](https://linear.app/aegisq/issue/AEG-372) |
| Wave B — M2.5 `cross_checker` (T9) | [AEG-373](https://linear.app/aegisq/issue/AEG-373) |
| Wave B — M2-pr GitHub RepoAdapter + PR | [AEG-374](https://linear.app/aegisq/issue/AEG-374) |
| Wave B — M3-hard gVisor + isolation | [AEG-375](https://linear.app/aegisq/issue/AEG-375) |
| Wave B — M3-encrypt Embargoed-case journal (T4) | [AEG-376](https://linear.app/aegisq/issue/AEG-376) |
| Wave B — M6 Intake adapters | [AEG-377](https://linear.app/aegisq/issue/AEG-377) |
| Wave C — M5-plugin + M8 SDK + supply chain | [AEG-378](https://linear.app/aegisq/issue/AEG-378) |
| Wave C — M7 MCP server | [AEG-379](https://linear.app/aegisq/issue/AEG-379) |
| Wave D — M9 Design-partner pilot + 0.1.0 | [AEG-380](https://linear.app/aegisq/issue/AEG-380) |

Memory cross-reference: stored in open-brain (project `AegisQ`) as memory `e9ff1ce5-a01a-435d-bd4e-76a047d6d2f3` for retrieval in future sessions.
