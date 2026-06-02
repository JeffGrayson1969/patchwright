# PatchWright — Product Requirements Document

> **Public release.** This PRD is the publishable version of the internal PatchWright PRD. A small number of sections — patent design-around rationale, named partner shortlists, internal estimation deltas — are held privately pending IP-counsel review and partner consent, and are marked **[Held privately]** below. The omitted material does not change the architecture or commitments.

| | |
|---|---|
| **Project** | PatchWright |
| **Version** | 0.2 (public release) |
| **Status** | Pre-design — for community review |
| **Document owner** | Jeff Grayson |
| **License (intended)** | Apache-2.0 (core); commercial license for Shield tier |

---

## 0. Change log

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-24 | Initial draft from landscape analysis + prior-art survey |
| 0.2 | 2026-05-25 | Addendum: MCP server as primary integration surface; `LLMProvider` abstraction; intake/ticket/repo adapter families; enterprise risk register R1–R10 mitigation placement |
| 0.2-public | 2026-06-01 | Public release; patent design-around rationale held privately pending IP-counsel review |

---

## 1. Vision

> **An open-source, model-agnostic agent runtime that turns a vulnerability finding — yours or someone else's — into a reviewed patch and a published advisory, with humans in the loop at every decision that matters, and with verifiable runtime integrity for the agents themselves.**

The "vulnpocalypse" is here: Anthropic's Glasswing has surfaced 10,000+ high/critical vulnerabilities across ~50 partners in a single month, and only a small fraction have been patched. NIST formally gave up on enriching most CVEs in April 2026. Maintainers are being DoS'd by AI-generated reports of varying quality. The bottleneck has moved decisively from *finding* bugs to *triaging, fixing, and disclosing* them — and the post-discovery loop is still running at human speed against machine-speed inflow.

The closed frontier labs (Glasswing, Big Sleep, Aardvark) and the commercial bolt-ons (Snyk Agent Fix, Copilot Autofix, AISLE, Mobb) leave a wide unowned middle: **no OSS-licensed, self-hostable, model-agnostic, end-to-end pipeline exists for an OSS maintainer, an enterprise AppSec team, or a disclosure coordinator to run themselves.** PatchWright fills that middle.

Crucially, the tool itself becomes a high-value attack surface — both because attackers will try to poison its agents to merge malicious patches or suppress disclosures, and because a compromised PatchWright deployment becomes a delivery vehicle into the supply chains it serves. The "Shield" tier addresses that head-on with hardware-attested sandboxes, signed-model verification, and SLSA-grade build provenance.

## 2. Problem statement

Five compounding bottlenecks in the post-discovery vulnerability loop:

| # | Bottleneck | Today | Target |
|---|---|---|---|
| **B1** | Triage of inbound AI-generated reports | Manual; maintainers swamped, slop indistinguishable from real reports | Agent dedups, validates exploitability in sandbox, scores reporter trust, packages structured human-review packet |
| **B2** | Reproduction | Manual; PoCs rarely complete or runnable | Agent spins up isolated sandbox, executes (or derives) PoC, records outcome with evidence |
| **B3** | Patch generation | Manual; ~2-week average for high/critical | Agent drafts patch + tests in a feature branch with human-readable rationale and provenance |
| **B4** | Advisory & CVE filing | Manual; CSAF/VEX/CVE Services workflows are heavyweight | Agent drafts CSAF + OpenVEX + GHSA/CVE submission from the patch diff; human approves before filing |
| **B5** | Downstream notification | Manual; security.txt contacts and distro maintainers pinged ad-hoc | Agent fans out notifications to security.txt contacts, downstream consumers, Dependency-Track instances, distro lists |

## 3. Goals & non-goals

### 3.1 Goals (v1.0)

1. End-to-end pipeline from a finding (inbound report or scanner output) to a reviewed patch PR and (optionally) a coordinated disclosure package.
2. Model-agnostic: works with Claude, GPT-5, Gemini, and at least one open-weight model.
3. Human-in-the-loop checkpoint at every state transition by default; auto-actions are explicit opt-in per-case-type.
4. Self-hostable on a maintainer's laptop or a $5 VPS.
5. Integrates with — does not replace — OSV, OpenVEX, CSAF, MITRE CVE Services, VINCE, Dependency-Track, OSV-Scanner, Trivy, Grype, Semgrep, CodeQL.
6. Append-only execution journal for every agent action; every state, decision, and artifact is replayable and auditable.
7. Strong runtime integrity for the agents themselves (see §8): signed/attested model artifacts, sandboxed sub-agents, supply-chain attestation for the PatchWright binary itself.
8. Resilient to prompt-injection, finding-poisoning, and AI-slop flooding (see §9 threat model).

### 3.2 Non-goals (v1.0)

- **Not a discovery engine.** PatchWright assumes the finding exists. Discovery is deliberately out of v1 scope; we ingest findings from other systems and humans.
- **Not a vulnerability database.** OSV exists, is OSS, and is the right schema. We consume it; we do not duplicate it.
- **Not a new SAST/SCA engine.** Semgrep, CodeQL, Trivy, Grype, OSV-Scanner are excellent. We orchestrate around them.
- **Not a new advisory format.** CSAF 2.0 and OpenVEX are the standards. We emit them; we do not invent a new one.
- **Not a hosted SaaS in v1.** Architecture must support a hosted runtime later (it's the Shield-tier delivery channel), but v1 ships as a self-hostable binary first.
- **Not a frontier discovery model.** We do not compete with Mythos/Aardvark/Big Sleep at the discovery layer. We complement them by handling what comes after.
- **Not an auto-merger.** No PatchWright default action auto-merges a patch or auto-files a CVE without human approval in v1.

## 4. Personas

### 4.1 Olivia — OSS maintainer
Solo maintainer of a load-bearing Python library (think `urllib3`-class). Day job elsewhere. Inbox has 14 unread "I found a CVE in your library" reports from various AI systems in the last week; she suspects most are slop but can't tell at a glance. Wants: a tool that triages those reports, flags the real ones, drafts a patch she can review in 20 minutes, and handles the CVE/advisory paperwork.

### 4.2 Marcus — Enterprise AppSec lead
Runs AppSec for a mid-sized fintech. Snyk, Semgrep, and CodeQL produce 2,000+ findings/week across 80 repos. Dev teams ignore most. Wants: ingest existing scanner output, deduplicate, reachability-prioritize, and emit *coordinated* PRs across affected repos with consistent fix patterns and an audit log his auditors can trust.

### 4.3 Priya — Vulnerability coordinator (PSIRT / CERT)
Coordinates disclosures across multiple vendors for a coalition (think CERT/CC or a vendor PSIRT handling shared dependencies). Manages embargo timers, multi-party communications, CSAF/VEX drafting, CVE Services filing, and downstream notification fan-out. Today: spreadsheets, Slack DMs, and VINCE. Wants: agent-drafted advisories, embargo timer state machine, integration with VINCE and CVE Services, and a clear audit trail for every decision.

### 4.4 Sam — Security-conscious adversary (anti-persona)
Wants to poison PatchWright into merging a malicious patch, suppressing a real disclosure, leaking embargoed information, or compromising the maintainers who run it. Submits adversarial reports designed to manipulate the triage agent. Tries to confuse the patch agent into introducing a subtle backdoor. Targets the runtime itself (model weights, prompt files, sandbox escapes). PatchWright's threat model (§9) is written against Sam.

## 5. User journeys

### 5.1 Olivia receives a vuln report

1. AI-generated report arrives at `security@olivia-library.org` (or via GitHub Security Advisory).
2. PatchWright's intake adapter parses the report, normalizes to OSV-Schema, and opens a case.
3. **Triage agent** runs: dedup against OSV/GHSA, classify report quality, score reporter trust, summarize the claim.
4. **Reproduce agent** runs in isolated sandbox: attempts the PoC; records outcome (reproduced / not reproduced / partial).
5. **Patch agent** runs: drafts a patch + tests in a feature branch; emits a human-readable evidence package.
6. **Olivia gets a notification:** "1 case ready for review." She runs `patchwright review <case-id>`.
7. Evidence package opens in her editor: the original report, the dedup result, the sandbox repro log, the patch diff, the test diff, the agent's reasoning trace, the confidence score.
8. She approves, edits, or rejects. If approved, the patch goes to a draft PR.
9. **Advisory agent** drafts CSAF + OpenVEX from the patch diff; reserves a CVE via MITRE CVE Services (pending her sign-off).
10. **Disclose agent** sets an embargo timer (default 90 days, configurable); upon embargo lift, files the CVE, publishes the advisory, and fans out notifications to security.txt contacts and Dependency-Track instances.

### 5.2 Marcus ingests scanner output

1. Cron runs `patchwright ingest --scanner snyk --org acme-fintech --since 24h`.
2. PatchWright normalizes Snyk output to OSV-Schema, dedupes against open cases, reachability-tags using Snyk's metadata.
3. For each surviving finding, the patch agent generates a candidate fix using the same per-repo conventions Marcus has configured (`patchwright.yaml`).
4. PatchWright emits a coordinated PR-set: one PR per affected repo, all linked via a parent "campaign" object.
5. Marcus's review queue shows the campaign; he can approve repo-by-repo or in bulk.
6. Every action — finding-ingest, dedup, patch-gen, PR-submission, approval — lands in the append-only journal. Marcus exports the journal to satisfy SOC 2 / ISO 27001 evidence requirements.

### 5.3 Priya coordinates a multi-party disclosure

1. Priya creates a case in VINCE; PatchWright's VINCE adapter mirrors it locally.
2. She adds affected vendors; PatchWright's contact adapter pulls each vendor's security.txt and pre-fills the multi-party communication thread.
3. **Advisory agent** drafts a CSAF document with placeholders for each vendor's affected products.
4. As each vendor confirms or denies impact, PatchWright's VEX agent generates corresponding OpenVEX statements and slots them into the CSAF.
5. Embargo timer is set; PatchWright tracks it and alerts Priya at T-7, T-2, T-day.
6. On embargo lift, PatchWright's CVE-Services adapter publishes the CVE and the advisory; the notification adapter fans out to all affected vendor and downstream contacts.
7. Priya approves every transition. Every transition is journaled.

## 6. Functional requirements

IDs use `FR-<area>-<n>`. Each requirement is mapped to the phase it must land in (P0/P1/P2/P3/P4 — see §13 roadmap).

### 6.1 Intake (FR-IN-*)

- **FR-IN-1** (P1): Parse inbound reports from email, GitHub Security Advisory, paste, and a generic JSON intake.
- **FR-IN-2** (P1): Normalize reports to OSV-Schema; preserve the raw original as an artifact.
- **FR-IN-3** (P2): VINCE-API adapter for coordination cases.
- **FR-IN-4** (P3): Scanner adapters for Snyk, Semgrep, CodeQL, Trivy, Grype, OSV-Scanner.
- **FR-IN-5** (P1): Reject reports that fail well-formedness validation, with a structured error returned to the reporter.

### 6.2 Triage (FR-TR-*)

- **FR-TR-1** (P1): Dedup new reports against open cases and against OSV/GHSA using **retrieval-augmented semantic similarity**.
- **FR-TR-2** (P1): Score reporter trust using rule-based signals (history with this project, signed commits, prior valid reports). No learned classifier in v1.
- **FR-TR-3** (P1): Generate a structured "triage packet": original report, dedup result, reporter score, plain-English summary of claim, agent's confidence, suggested disposition.
- **FR-TR-4** (P2): Detect AI-slop signatures (hallucinated CVEs, fabricated PoCs that don't compile, references to nonexistent functions) and flag as such.

### 6.3 Reproduction (FR-RP-*)

- **FR-RP-1** (P1): Sandboxed PoC execution in isolated container (gVisor or Firecracker, per §10).
- **FR-RP-2** (P1): Record sandbox outcome with full stdio, exit code, and environmental snapshot.
- **FR-RP-3** (P2): Agent attempts to derive a minimal PoC from a textual description if no PoC was supplied.
- **FR-RP-4** (P2): Differential repro: confirm the bug exists on the affected version and is absent on a candidate patched version.

### 6.4 Patch generation (FR-PT-*)

- **FR-PT-1** (P1): **Two-phase patch generation** — Phase A: LLM produces a natural-language change *plan*; Phase B: deterministic codemod / AST rewriter applies the plan to files.
- **FR-PT-2** (P1): Generated patch must include corresponding test(s) that fail before patch and pass after.
- **FR-PT-3** (P1): Patch produced as a feature branch + draft PR; never auto-merged in v1.
- **FR-PT-4** (P2): Multi-language support: Python, JS/TS, Go, Rust, Java, C/C++.
- **FR-PT-5** (P3): Multi-repo coordinated patch campaigns (one finding, N repos).
- **FR-PT-6** (P3): Backport / override generation for EOL packages and transitive deps with no upstream fix.

### 6.5 Human review (FR-HR-*)

- **FR-HR-1** (P1): CLI review workflow: `patchwright review <case-id>` opens the evidence package in `$EDITOR` (markdown).
- **FR-HR-2** (P1): Approve / edit / reject / fork verbs; rejection requires a one-line reason.
- **FR-HR-3** (P1): Every review action lands in the journal.
- **FR-HR-4** (P3): Web review UI (FastAPI + HTMX); Slack and Linear adapters.
- **FR-HR-5** (P2): Configurable review checkpoints per case type (`patchwright.yaml`). Defaults: review at triage, patch, advisory, and disclose transitions.

### 6.6 Advisory & disclosure (FR-AD-*)

- **FR-AD-1** (P2): CSAF 2.0 advisory drafting from patch diff + case state.
- **FR-AD-2** (P2): OpenVEX statement generation tied to fix commits.
- **FR-AD-3** (P2): MITRE CVE Services adapter for ID reservation + publication.
- **FR-AD-4** (P2): Embargo timer state with configurable defaults (90-day OSS, 14-day critical).
- **FR-AD-5** (P2): Notification fan-out to security.txt contacts, distro lists, and Dependency-Track instances on embargo lift.
- **FR-AD-6** (P3): EPSS / KEV-aware prioritization for notification ordering.

### 6.7 Provenance & journaling (FR-PV-*)

- **FR-PV-1** (P1): Append-only JSONL execution journal — every agent invocation, tool call, file written, and human decision.
- **FR-PV-2** (P1): Journal is the **primary state store** — agents rehydrate from journal artifacts on every invocation.
- **FR-PV-3** (P1): Journal entries are content-addressed (SHA-256) and chained (Merkle).
- **FR-PV-4** (P2): Journal signing — every entry signed by the operator's key.
- **FR-PV-5** (P2): Journal export in in-toto attestation format for supply-chain integration.

### 6.8 Configuration & extensibility (FR-CF-*)

- **FR-CF-1** (P1): Per-project `patchwright.yaml` (model provider, sandbox backend, review checkpoints, embargo defaults, project conventions).
- **FR-CF-2** (P1): Plugin SDK for adapters (intake, scanner, advisory, notification).
- **FR-CF-3** (P2): Custom prompts per agent, versioned and signed.
- **FR-CF-4** (P3): Per-case-type policy overrides.

## 7. Non-functional requirements

### 7.1 Performance
- **NFR-P-1**: Triage of a typical inbound report < 60s wall clock.
- **NFR-P-2**: Patch generation for a typical CVE < 5 minutes wall clock.
- **NFR-P-3**: CLI commands (`status`, `list`, `review`) < 200ms p50.

### 7.2 Reliability
- **NFR-R-1**: All agent actions are idempotent — re-running on the same journal produces the same result.
- **NFR-R-2**: Crash mid-action does not corrupt journal; orchestrator replays from last good entry.
- **NFR-R-3**: No data loss on disk-full or kill -9; all writes are journal-first.

### 7.3 Portability
- **NFR-T-1**: Runs on Linux x86_64, ARM64, and macOS for development.
- **NFR-T-2**: Single self-contained install (`pipx install patchwright` or single binary via PyInstaller).
- **NFR-T-3**: Backend stores: SQLite (single-maintainer), Postgres (team/coordinator).

### 7.4 Observability
- **NFR-O-1**: Structured logging (JSON) for all agent and orchestrator activity.
- **NFR-O-2**: OpenTelemetry traces optional.
- **NFR-O-3**: `patchwright explain <case-id>` renders the full decision tree for a case.

### 7.5 Maintainer ergonomics
- **NFR-M-1**: Zero-config default for OSS maintainers: `patchwright init` produces a working config for a typical GitHub-hosted Python/JS/Rust/Go project.
- **NFR-M-2**: All defaults are conservative (human-in-loop, no auto-action, 90-day embargo, no telemetry).

## 8. Security & runtime integrity requirements (NFR-S-*)

These are the differentiators and the basis for the paid Shield tier.

### 8.1 Sandboxing
- **NFR-S-1**: Sub-agents (especially reproduction and patch-application) run in isolated sandboxes. Core ships gVisor/Firecracker config; Shield adds Confidential Computing (TEE) configurations and remote attestation.
- **NFR-S-2**: Network policy default-deny in sandboxes; allowlist per case.
- **NFR-S-3**: Filesystem default-read-only outside an explicit case scratch directory.

### 8.2 Model integrity
- **NFR-S-4**: Model artifacts (prompts, codemod rules, classifier weights) are content-addressed and signed.
- **NFR-S-5**: Core verifies signatures on built-in artifacts at startup; Shield adds runtime attestation that the loaded model in inference is the model that was signed.
- **NFR-S-6**: BYO-model support requires explicit operator approval; the journal records the model provider, version, and (where available) the provider's published model card hash.

### 8.3 Supply chain
- **NFR-S-7**: PatchWright binaries are built reproducibly and published with SLSA Level 3 attestations.
- **NFR-S-8**: Plugins must be signed; default config refuses unsigned plugins. (Shield can require Sigstore-keyless signing through a specific Fulcio root.)
- **NFR-S-9**: Dependency pinning + automated provenance verification (osv-scanner, Sigstore cosign).

### 8.4 Secrets handling
- **NFR-S-10**: API keys (model providers, GitHub, CVE Services) stored via OS keychain or HashiCorp Vault adapter; never in plain config.
- **NFR-S-11**: Journal entries scrub known-secret patterns before write.

### 8.5 Privacy
- **NFR-S-12**: No telemetry by default. Opt-in telemetry collects aggregated counts only, never source code, patches, or report contents.
- **NFR-S-13**: When reproducing PoCs, the operator is told explicitly which model provider's API will see what data, and can opt to use only on-device models.

## 9. Threat model

PatchWright's value depends on whether the actions it takes (triage, patch, file CVE) can be trusted. Sam (anti-persona §4.4) attacks at every layer.

| ID | Threat | Vector | Impact | Mitigation |
|---|---|---|---|---|
| **T1** | **Malicious patch via poisoned report** | Adversarial report crafts an LLM-friendly description that nudges the patch agent to introduce a subtle backdoor. | Compromised upstream dependency. | (a) Two-phase patch generation: LLM emits *plan*, deterministic codemod applies it — codemod refuses risky operations (e.g., network exfil insertion, eval). (b) Test agent runs the generated tests *plus* a fuzz round against the patched code. (c) Mandatory human review before PR open. (d) Plan + diff are journaled and signed. |
| **T2** | **Prompt injection** | Report body, README, source comments, or third-party advisory text contains instructions overriding the agent's prompt. | Agent leaks embargoed info, suppresses real reports, files spurious CVEs. | (a) Strict prompt-injection delimiters; all user-supplied text wrapped and labeled. (b) Sub-agents see only the minimum slice of input they need. (c) Output validation: agent outputs are validated against schemas (Pydantic / JSON Schema) before any action. (d) Cross-checker agent reviews the primary agent's plan against original input. |
| **T3** | **AI-slop flood DoS** | Attacker (or just chaos) sends thousands of low-quality AI-generated reports. | Maintainer overwhelmed; real reports lost. | (a) Triage agent rate-limits per reporter. (b) Reporter trust score weights queue position. (c) Low-trust reports require PoC sandbox-reproducibility before agent advances them. |
| **T4** | **Embargo leak via journal exfil** | Attacker steals or subpoenas the journal during embargo period. | Premature disclosure of an unpatched bug. | (a) Journal-at-rest encryption (age / sops). (b) Per-case scoping: embargoed cases live in encrypted-volume scratch dirs. (c) Operator-rotated keys. (d) Shield: optional TEE-bound journal. |
| **T5** | **Compromised model weights / prompts** | Attacker replaces an agent's prompt or model file with a malicious version. | Agent behavior subverted invisibly. | (a) Signed artifacts (NFR-S-4/5). (b) Startup verification; refuse to run if mismatch. (c) Shield: runtime attestation. |
| **T6** | **Sandbox escape during repro** | PoC code escapes the sandbox onto the maintainer's host. | Maintainer machine compromised; attacker gains git push rights. | (a) gVisor/Firecracker isolation. (b) No host-mount writes. (c) Network default-deny. (d) Shield: hardware-backed isolation (SEV-SNP / TDX) for hosted-runtime customers. |
| **T7** | **CVE filing impersonation** | Agent files a CVE on behalf of a project without authorization. | Reputational harm; spurious public advisory. | (a) Default never-auto-file in v1. (b) Operator must hold CVE Services credentials. (c) CSAF/CVE drafts are dry-run by default. |
| **T8** | **Supply-chain attack on PatchWright itself** | Malicious package in PatchWright's own dep tree. | Compromise propagates to every PatchWright deployment. | (a) Pinned deps + osv-scanner CI. (b) SLSA Level 3 builds. (c) Cosign-verified releases. (d) Reproducible-build verification by community. |
| **T9** | **Model-provider compromise / hostile prompt** | The hosted LLM provider returns a malicious response (compromised provider, or jurisdiction-driven backdoor). | Triage decisions or patches subtly subverted. | (a) Cross-checker agent on a *different* provider validates critical outputs. (b) Determinism: codemod step does not delegate to LLM. (c) Shield: option to require multi-provider agreement on critical decisions. |
| **T10** | **Reporter de-anonymization** | Journal leaks reporter identity. | Chilling effect on disclosure; legal risk for reporters in hostile jurisdictions. | (a) Reporter-identity field encrypted at rest, accessible only to operator. (b) Pseudonymous reporter IDs in public artifacts. |

## 10. Architecture

### 10.1 Core architectural commitments

1. **Finite-state-machine orchestrator** with explicit, named states. Every case is at exactly one state. State transitions are atomic and journaled.
2. **Append-only JSONL execution journal as the primary state store.** The orchestrator does not maintain a separate "engine state"; it reconstructs all state by replaying the journal.
3. **Sub-agents rehydrate from disk artifacts** on every invocation. There is no long-lived in-memory agent context — the journal *is* the context.
4. **Two-phase patch generation**: Phase A LLM emits a NL plan; Phase B deterministic codemod/AST tool applies the plan. The LLM does not directly write file mutations.
5. **Retrieval-augmented semantic dedup** for triage, not learned classification.
6. **Cross-checker agents** for critical outputs (T9): a second agent on a different provider re-evaluates the primary's recommendation before any state transition that affects the outside world.
7. **All adapter boundaries are plugins.** Even the model-provider call is a plugin. This is the model-agnosticism layer.

### 10.2 Component map (v1.0)

- **Orchestrator** (`patchwright.core.orchestrator`) — the FSM driver, journal manager, plugin loader.
- **Agents** (`patchwright.agents.*`) — `triage`, `reproduce`, `patch_plan`, `patch_apply`, `test`, `advisory`, `disclose`, `cross_checker`. Each is a stateless function that takes case state + tools and returns a proposed transition + evidence.
- **Tools** (`patchwright.tools.*`) — wrappers for git, codemod libraries (e.g., LibCST, Comby, ts-morph), sandbox runners, osv-scanner, etc.
- **Adapters** (`patchwright.adapters.*`) — intake (email/IMAP, GitHub, paste, VINCE), scanner (Snyk/Semgrep/CodeQL/Trivy/Grype/OSV-Scanner), advisory output (CSAF/OpenVEX/CVE Services/GHSA), notification (security.txt/Dependency-Track/distro lists), review surface (CLI/Web/Slack/Linear).
- **Model providers** (`patchwright.models.*`) — Anthropic, OpenAI, Google, vLLM/local, Ollama.
- **Storage** (`patchwright.storage.*`) — SQLite/Postgres for case metadata, content-addressed object store for artifacts, JSONL journal.
- **CLI** (`patchwright.cli`) — `init`, `ingest`, `list`, `review`, `explain`, `journal`, `serve`.

### 10.3 Data model (sketch)

```yaml
Case:
  id: case-2026-05-24-0001
  state: awaiting_human_review_patch
  created_at: 2026-05-24T18:21:13Z
  reporter:
    id: pseudonymous-hash
    trust_score: 0.42
  origin:
    type: inbound_email | github_advisory | scanner | manual
    raw_artifact_id: sha256:abc...
  finding:
    osv_normalized: { ... }
    cve_id: null
  artifacts:
    - id: sha256:def...
      kind: triage_packet
    - id: sha256:ghi...
      kind: repro_log
    - id: sha256:jkl...
      kind: patch_plan
    - id: sha256:mno...
      kind: patch_diff
  journal_entries:
    - hash: sha256:...
      kind: ingest | triage | repro | patch_plan | patch_apply | review | advisory | disclose
      author: agent:triage | human:olivia
      content_ref: sha256:...
      signature: ...
```

### 10.4 Plugin model

Every adapter, agent, tool, and model provider is a plugin. The default install ships a curated set of first-party plugins. Third-party plugins are loaded only if signed with a trusted key; the default trust store includes the PatchWright project's Fulcio root, and operators can add their own.

## 11. Patent strategy

**[Held privately]** Several design decisions in this PRD are deliberate engineering-arounds based on a pre-counsel prior-art scan. The specific patent prior-art map, the per-patent design-around rationale, and the planned defensive publications are tracked privately pending IP-counsel review and will be published once counsel has signed off and any necessary defensive publications have been filed.

PatchWright plans to pursue memberships in **OpenSSF**, **Open Invention Network (OIN)**, and **LOT Network** from day one. Apache-2.0 was chosen in part for its patent retaliation clause.

## 12. OSS core vs. paid Shield tier

The split mirrors the OSS-plus-commercial-shield pattern (analogous to AegisQ CodeShield).

### 12.1 OSS core (Apache-2.0)

Everything required to run the full pipeline. A maintainer or AppSec team can self-host the entire product. Specifically:

- Full FSM orchestrator, all v1 agents, all v1 adapters.
- Plugin SDK.
- Signed-artifact verification at startup.
- gVisor / Firecracker sandbox configurations.
- Append-only journal with signing.
- CLI, web review UI, Slack/Linear adapters.
- BYO-model with Claude, GPT-5, Gemini, vLLM, Ollama.

### 12.2 Paid Shield tier (commercial license)

High-assurance runtime guarantees for organizations whose threat model includes nation-state, regulated industries, or that need third-party-attestable trust. Specifically:

- **Attested sandboxes** — hardware-backed isolation (AMD SEV-SNP, Intel TDX); remote attestation reports.
- **Signed-model runtime attestation** — proof at inference time that the loaded model in the LLM provider's serving infrastructure matches a signed manifest.
- **Confidential journaling** — TEE-bound, encrypted, with attestation hooks for compliance auditors.
- **Multi-provider consensus agents** — Shield ships a "consensus" cross-checker that requires N-of-M provider agreement on critical decisions; OSS ships single-provider cross-check only.
- **Hosted runtime** — managed, multi-tenant PatchWright with SSO/SAML, RBAC, audit-grade telemetry, SLA.
- **Curated signed plugin registry** — vetted third-party adapters, signed under PatchWright's curated Fulcio root.
- **Premium model integrations** — direct access to frontier-model preview tiers via PatchWright's commercial agreements.
- **Customer success & SLA** — uptime, response-time, breach-disclosure commitments.

### 12.3 What stays out of the OSS core

Almost nothing. The deliberate principle is that *the OSS core is feature-complete for self-hosting*; the Shield tier adds **assurance, attestation, and operational SLA**, not capability.

### 12.4 Contributor agreement & relicensing

- Apache-2.0 with a Developer Certificate of Origin (DCO) — no CLA in v1.
- The Shield tier is built on the OSS core *plus* proprietary modules; it does not relicense the core.
- If we ever want to relicense (e.g., to move the core to a foundation), DCO + Apache-2.0 makes that feasible without contributor-by-contributor sign-off.

## 13. Phased roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 — Spike** | Repo, license, code of conduct, security.txt, orchestrator skeleton, one trivial agent. Identify 1 OSS maintainer design partner. | Hello-world case completes; design partner committed. |
| **P1 — Triage + patch for OSS maintainers** | FR-IN-1/2/5, FR-TR-1/2/3, FR-RP-1/2, FR-PT-1/2/3, FR-HR-1/2/3, FR-PV-1/2/3, FR-CF-1/2, NFR-S-1/4/7/8/10. | Design partner ships a real patch from a real report through PatchWright. |
| **P2 — Disclosure orchestration** | FR-IN-3, FR-RP-3/4, FR-PT-4, FR-HR-5, FR-AD-1/2/3/4/5, FR-PV-4/5, FR-CF-3, NFR-S-2/3/5/6/11/12/13. | One CVE published end-to-end via PatchWright with all artifacts. |
| **P3 — Enterprise & coordinator adapters** | FR-IN-4, FR-PT-5/6, FR-HR-4, FR-AD-6, FR-CF-4. Web UI, Slack, Linear. | Marcus persona pilot: 50+ findings ingested, coordinated PR-set shipped. |
| **P4 — Discovery & Shield tier GA** | Optional IronCurtain-style discovery; OSS-Fuzz-Gen integration. Shield tier productized. | First paying Shield customer. |

Phase durations are tracked internally and revised as Phase 0 lands. The headline shape: Phase 0 is weeks; P1–P3 are months; P4 is open-ended.

## 14. Success metrics

Public targets, scaled by phase:

| Metric | P1 | P2 | P3 |
|---|---|---|---|
| Active OSS maintainers self-hosting | 10 | 100 | 1,000 |
| End-to-end cases completed | 50 | 500 | 5,000 |
| Median time from report → reviewed-patch-PR | < 8h | < 4h | < 2h |
| CVEs filed via PatchWright | 0 (not yet supported) | 25 | 250 |
| Reporter-trust-weighted slop reduction | n/a | 3× | 5× |
| Security incidents in PatchWright itself | 0 | 0 | 0 |

## 15. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Closed competitor goes free-for-OSS | M | H | Differentiate on OSS license, self-host, model-agnostic, disclosure coverage, Shield-tier assurance. |
| Patent assertion from a sleeping competitor | M | H | Pre-counsel design-around (held privately, §11); join OIN/LOT; defensive publications; Apache-2.0 patent retaliation. |
| Frontier-model providers restrict "offensive security" workloads | M | H | Model-agnostic from day one; open-weight fallback; document workflow as defensive, not offensive. |
| Triage agent fooled by adversarial reports | H | H | Cross-checker; sandbox repro mandatory; rule-based scoring not learned. T1/T2/T3 mitigations. |
| Maintainer community rejects "more AI in the inbox" | H | H | First public artifact must be a *triage* win for a respected maintainer, not a patch win. Coordinate with OSSF AI-SLOP WG. |
| Sandbox escape causes real-world compromise | L | Critical | Defense-in-depth: gVisor + Firecracker + network-deny + ephemeral; quarterly red-team. |

## 16. Open questions

1. Foundation home — OpenSSF, OWASP, Linux Foundation.
2. Initial model defaults.
3. Patch language priorities for v1.
4. CNA status pursuit vs. always filing through MITRE / a sponsor.

Partner-specific open questions (which OSS maintainer, which enterprise AppSec team, which coordinator) are **[Held privately]** pending partner consent.

## 17. Glossary

- **CNA** — CVE Numbering Authority.
- **CSAF** — Common Security Advisory Framework (OASIS).
- **CVE** — Common Vulnerabilities and Exposures (MITRE).
- **CVSS** — Common Vulnerability Scoring System (FIRST).
- **EPSS** — Exploit Prediction Scoring System (FIRST).
- **GHSA** — GitHub Security Advisory.
- **KEV** — Known Exploited Vulnerabilities catalog (CISA).
- **NVD** — National Vulnerability Database (NIST).
- **OSV** — Open Source Vulnerabilities schema and aggregator (OpenSSF / Google).
- **PoC** — Proof of Concept exploit.
- **PSIRT** — Product Security Incident Response Team.
- **SBOM** — Software Bill of Materials.
- **SLSA** — Supply-chain Levels for Software Artifacts.
- **TEE** — Trusted Execution Environment.
- **VEX** — Vulnerability Exploitability eXchange.
- **VINCE** — Vulnerability Information and Coordination Environment (CERT/CC).

## 18. References

- [Anthropic — Project Glasswing initial update](https://www.anthropic.com/research/glasswing-initial-update)
- [OSV.dev](https://osv.dev/) · [OSV Schema](https://ossf.github.io/osv-schema/)
- [OASIS CSAF 2.0](https://www.csaf.io/)
- [OpenVEX (OpenSSF)](https://openssf.org/projects/openvex/)
- [MITRE CVE Services](https://github.com/CVEProject/cve-services)
- [CERT/CC VINCE](https://github.com/CERTCC/VINCE)
- [SLSA framework](https://slsa.dev/) · [Sigstore](https://www.sigstore.dev/)
- [Niels Provos — IronCurtain](https://provos.org/p/finding-zero-days-with-any-model)

---

## Addendum — v0.2 (May 25, 2026)

This addendum extends parts of §3, §10, §12, and §13.

### A.1 — CodeShield-pattern architecture

PatchWright mirrors **AegisQ CodeShield's three-pillar integration model**:

1. **MCP server as primary integration surface** — `patchwright serve --mcp` exposes 8 tools (`intake_report`, `triage_case`, `reproduce_poc`, `generate_patch_plan`, `apply_patch`, `draft_advisory`, `get_status`, `explain_case`). Every MCP-aware AI tool (Claude Code, Cursor, Windsurf, Cline, Continue, Roo Code, Aider, Codex CLI, Copilot Workspace) drives PatchWright for free, no per-tool adapter required.
2. **CLI as secondary surface** — for direct human/script use, scheduled jobs, CI.
3. **IDE extensions (Phase 2/3)** — VS Code first.

### A.2 — Two operating modes

| Mode | Driver | LLM source | Use case |
|---|---|---|---|
| **A · PatchWright drives** | Internal FSM | `AnthropicProvider` or `OpenAIProvider` | Solo maintainer running locally; scheduled jobs; CI |
| **B · MCP host drives** | Claude Code / Cursor / Windsurf / Cline / etc. | `MCPSamplingProvider` (host's LLM via MCP Sampling) | Developer inside an AI IDE |

Both modes share the FSM, journal, codemod catalog, and review surface.

### A.3 — `LLMProvider` interface

Three implementations in Phase 1:

1. **`AnthropicProvider`** — `anthropic` SDK.
2. **`OpenAIProvider`** — `openai` SDK with `base_url` config; supports Groq, OpenRouter, Together, Ollama, vLLM, Azure OpenAI, AWS Bedrock (via proxy) through the single env var `PATCHWRIGHT_LLM_BASE_URL`.
3. **`MCPSamplingProvider`** — delegates to host MCP client's LLM via MCP Sampling.

All agents call through this interface. Default provider is operator-configurable via `patchwright.yaml`.

### A.4 — Three adapter families

- **`IntakeAdapter`** — email, GitHub Security Advisory, JSON, **SARIF**, AI-scanner vendor formats, **`LLMParseAdapter`** for arbitrary text, VINCE, HackerOne, Bugcrowd.
- **`TicketAdapter`** — Linear (P1), GitHub Issues, Jira (P2), GitLab Issues (P2), ServiceNow (P3), Azure DevOps (P3).
- **`RepoAdapter`** — GitHub (P1), GitLab (P2), Bitbucket (P2), Azure Repos (P3), AWS CodeCommit (P3), Gerrit (P3), Perforce (P3).

All three are PEP 544 Protocols; plugins live under `patchwright.plugins.{intake,ticket,repo}` entry points.

### A.5 — Enterprise risk register

| Risk | Where mitigated |
|---|---|
| **R1: Source code leaving network** | **OSS core** via `OpenAIProvider` base-URL pattern (point at local Ollama/vLLM) |
| **R2: Embargoed CVE data exposure** | **OSS core** via `embargo_mode: strict` — hard-fail any non-local LLM call for embargoed cases, no opt-out flag |
| **R3: Patch liability** | OSS core: human-in-loop default + two-phase patching + signed audit trail. Shield: legal disclaimer language + E&O insurance recommendations |
| **R4: Data residency / sovereignty** | **Shield tier** via `--profile air-gapped` + regional endpoint config + eBPF egress monitor |
| **R5: Supply-chain attack on PatchWright** | OSS: SLSA L3, pinned deps, cosign. Shield: reproducible-build community attestation, signed plugin registry |
| **R6: Multi-tenant isolation (Shield hosted)** | **Shield tier only** — per-tenant KMS keys, namespace isolation, compute isolation |
| **R7: Compliance frameworks** | Shield: SOC 2 / ISO 27001 / SOX / FedRAMP control matrices + evidence-pack generator |
| **R8: Insider threat** | OSS: signed journal. Shield: RBAC, SSO/SAML, separation of duties for high-sev, break-glass |
| **R9: LLM provider dependency** | OSS: model-agnostic via `LLMProvider`. Shield: multi-provider consensus, cost caps, failover ladder |
| **R10: Regulatory (EU AI Act / CRA / DORA / NIS2 / CIRCIA)** | Shield: gap analysis + technical-file template |

### A.6 — Phase 2 enterprise pilot

**[Held privately]** Phase 2 enterprise-pilot vertical and partner are tracked internally and will be announced when the pilot commits. The Phase 2 adapter set (Jira, GitLab, Bitbucket) is sequenced to land regardless of which vertical lands first.

### A.7 — Revised roadmap shape

The net effect of adopting the CodeShield pattern (MCP server primary, `LLMProvider` abstraction, intake/ticket/repo adapter families) plus the R1/R2 enterprise risk mitigations is to broaden Phase 1 and Phase 2 modestly. In exchange we get:

- Universal AI-tool support via MCP.
- Enterprise credibility from day one.
- The R1/R2 mitigations that prevent project-killing incidents.
- A Shield-tier product that mirrors a proven commercial pattern.

---

**End of PRD v0.2-public**
