# PatchWright

> Open-source, model-agnostic agent runtime that turns a vulnerability finding into a reviewed patch and a published advisory.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#project-status)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

---

## Project status

**Pre-alpha. Phase 0 — Spike.** There is no installable release yet. This repository currently contains the [PRD](PRD.md), the Phase 0 spike plan, and the engineering scaffold. Watch the repo or open an issue if you want to be notified when the first usable build lands.

---

## Why this exists

Anthropic's Glasswing and OpenAI's Aardvark have demonstrated that frontier models can find serious vulnerabilities at machine speed — Glasswing's initial update reports 10,000+ high/critical findings across roughly 50 partners in a single month. The bottleneck has shifted: the open question is no longer "can AI find bugs?" but "who triages, patches, files, and discloses them — fast enough to matter?"

Existing answers leave a gap. The closed frontier labs and commercial bolt-ons (Snyk Agent Fix, Copilot Autofix, AISLE, Mobb, ZeroPath) are excellent but proprietary. NIST formally [stopped enriching most CVEs in early 2026](https://nvd.nist.gov/general/news). OSS maintainers are being overwhelmed by AI-generated reports of varying quality, and there is no OSS-licensed, self-hostable, model-agnostic, end-to-end pipeline they can run themselves.

PatchWright fills that middle.

---

## What PatchWright does

PatchWright is a state-machine orchestrator that drives a vulnerability finding through five stages, with a human-review checkpoint at each transition by default:

```
            ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
finding ──▶ │  Triage  │──▶│ Reproduce│──▶│  Patch   │──▶│ Advisory │──▶│ Disclose │──▶ CVE + notifications
            └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
                  ▲              ▲              ▲              ▲              ▲
                  └──── human-in-the-loop review at every transition by default ────┘
```

- **Triage.** Deduplicate against OSV / GHSA, score reporter trust, classify report quality, summarize the claim.
- **Reproduce.** Run the supplied PoC in a sandboxed container (gVisor / Firecracker); record outcome, stdio, and environment.
- **Patch.** *Two-phase generation* — the LLM emits a natural-language change plan; a deterministic codemod (LibCST / Comby / ts-morph) applies it. Tests that fail before the patch and pass after are produced alongside the diff.
- **Advisory.** Draft CSAF 2.0 + OpenVEX from the patch diff; optionally reserve a CVE via MITRE CVE Services.
- **Disclose.** Run the embargo timer; on lift, file the CVE, publish the advisory, fan out notifications to `security.txt` contacts and Dependency-Track instances.

Every transition lands in an append-only, hash-chained, signed journal that doubles as the system's only state store. Re-running on the same journal produces the same result.

---

## What PatchWright is *not*

- **Not a discovery engine.** It assumes the finding exists. Discovery is out of scope for v1; PatchWright ingests findings from OSV, GHSA, scanners, and humans.
- **Not a vulnerability database.** OSV exists, is OSS, and is the right schema.
- **Not a new SAST/SCA engine.** Semgrep, CodeQL, Trivy, Grype, and OSV-Scanner are excellent. PatchWright orchestrates around them.
- **Not a new advisory format.** CSAF 2.0 and OpenVEX are the standards.
- **Not an auto-merger.** No default action auto-merges a patch or auto-files a CVE without human approval.

---

## Architecture commitments

1. **Finite-state-machine orchestrator** with explicit, named states. Every case is at exactly one state. Transitions are atomic and journaled.
2. **Journal as the primary state store.** The orchestrator does not maintain a separate engine state; it reconstructs everything by replaying an append-only, content-addressed, Merkle-chained JSONL journal.
3. **Sub-agents rehydrate from disk artifacts** on every invocation. There is no long-lived in-memory agent context — the journal *is* the context.
4. **Two-phase patch generation.** The LLM produces a natural-language plan; a deterministic AST rewriter applies it. The LLM does not directly write file mutations.
5. **Retrieval-augmented semantic dedup for triage**, not learned classification.
6. **Cross-checker agents** for critical outputs — a second agent on a different provider re-evaluates the primary's recommendation before any state transition that affects the outside world.
7. **Every adapter boundary is a plugin.** Including the model-provider call. This is the model-agnosticism layer.

See [PRD §10](PRD.md#10-architecture) for the full architectural rationale.

---

## Personas

- **Olivia** — Solo OSS maintainer being DoS'd by AI-generated vulnerability reports. Wants triage that flags the real ones.
- **Marcus** — Enterprise AppSec lead drowning in 2,000+ scanner findings per week across 80 repos. Wants coordinated PR-sets with a trustworthy audit log.
- **Priya** — Vulnerability coordinator running multi-party disclosures. Wants CSAF/VEX drafting, embargo state, and CVE Services integration without spreadsheets.
- **Sam** — Anti-persona. Tries to poison PatchWright into merging a backdoored patch or suppressing a real disclosure. The [threat model](PRD.md#9-threat-model) is written against Sam.

---

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| **P0 — Spike** | Repo, license, governance, orchestrator skeleton, one trivial agent, hello-world end-to-end | **In progress** |
| **P1 — Triage + Patch MVP** | Intake, dedup, sandboxed repro, two-phase patch, CLI review, MCP server, model-agnostic `LLMProvider` | Planned |
| **P2 — Disclosure orchestration** | CSAF / OpenVEX / CVE Services, embargo timer, notification fan-out, SARIF + AI-scanner intake | Planned |
| **P3 — Enterprise & coordinator adapters** | VINCE, Web UI, Slack/Linear, Jira/GitLab/Bitbucket, multi-repo coordinated patch campaigns | Planned |
| **P4 — Discovery hooks + Shield tier GA** | Optional IronCurtain-style discovery; commercial Shield tier productized | Planned |

See [`phase0-spike-plan.md`](phase0-spike-plan.md) and [`phase1-work-plan.md`](phase1-work-plan.md) for the detailed work breakdowns.

---

## OSS core and the Shield tier

PatchWright is dual-licensed in a familiar OSS pattern: the **OSS core is feature-complete for self-hosting** under Apache-2.0; a separate commercial **Shield tier** (provided by AegisQ) adds assurance, attestation, and operational SLA — not capability.

Specifically:

| | OSS core (Apache-2.0) | Shield tier (commercial) |
|---|---|---|
| Full FSM, all agents, all adapters | ✅ | ✅ |
| Self-hostable on a laptop or a VPS | ✅ | ✅ |
| Append-only signed journal | ✅ | ✅ |
| gVisor / Firecracker sandboxing | ✅ | ✅ |
| BYO model (Claude, GPT-5, Gemini, vLLM, Ollama, …) | ✅ | ✅ |
| MCP server (Claude Code, Cursor, Cline, …) | ✅ | ✅ |
| Hardware-attested sandboxes (SEV-SNP, TDX) | — | ✅ |
| Signed-model runtime attestation | — | ✅ |
| Multi-provider consensus agents | — | ✅ |
| Hosted multi-tenant runtime with SSO/SAML/RBAC | — | ✅ |
| Compliance evidence packs (SOC 2, ISO 27001, FedRAMP) | — | ✅ |

The principle: **the OSS core does not get crippled to sell the Shield tier.** Shield exists for organizations whose threat model includes nation-state attackers, regulated industries, or third-party-attestable trust.

---

## Standards consumed and emitted

PatchWright integrates with — does not replace — existing standards:

- **Consumed:** OSV-Schema, CycloneDX SBOM, GHSA, SARIF, Snyk / Semgrep / CodeQL / Trivy / Grype output
- **Emitted:** CSAF 2.0, OpenVEX, MITRE CVE Services records, in-toto attestations
- **Coordination:** CERT/CC VINCE, security.txt (RFC 9116), Dependency-Track

See [`standards-deep-read.md`](standards-deep-read.md) for the detailed standards review.

---

## Security

PatchWright is itself a high-value attack surface. Reporting:

- See [`SECURITY.md`](SECURITY.md) and [`.well-known/security.txt`](.well-known/security.txt) for the disclosure policy.
- The threat model lives in [PRD §9](PRD.md#9-threat-model) and is written against an explicit anti-persona (Sam) at every layer: prompt injection, finding poisoning, AI-slop flood DoS, embargo leak, model-weight tampering, sandbox escape, supply chain.
- Releases are reproducibly built and published with SLSA Level 3 provenance.

---

## Contributing

We welcome contributions. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first.

- **License:** Apache-2.0.
- **Sign-off:** Developer Certificate of Origin (DCO) required on every commit (`git commit -s`). No CLA.
- **Commits:** Conventional Commits.
- **Code of Conduct:** [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
- **Good-first-issue tag** marks tickets sized for new contributors.

If you maintain an OSS project being hit by AI-generated vulnerability reports and you'd like to be a design partner for Phase 1, please open a discussion or email `partners@patchwright.dev`.

---

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
