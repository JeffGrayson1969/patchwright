# Security Policy

PatchWright is a security tool. We take vulnerabilities in PatchWright itself extremely seriously — the threat model in [PRD §9](PRD.md#9-threat-model) is explicit that a compromised PatchWright deployment becomes a delivery vehicle into the supply chains it serves.

## Reporting a vulnerability

**Please do not file a public GitHub issue for a security vulnerability.**

Two reporting channels:

1. **GitHub Security Advisories (preferred)** — use the "Report a vulnerability" button in the Security tab of this repo. This gives you a private channel with the maintainers and a pre-filled advisory template.
2. **Email** — `security@patchwright.dev`. PGP key fingerprint and Signal contact will be listed at `https://patchwright.dev/.well-known/security.txt` when the domain goes live.

Please include:

- A description of the issue and the affected version (`patchwright --version`).
- Reproduction steps or a minimal PoC.
- The impact you observed or believe is possible.
- Whether you've already disclosed the issue elsewhere and any existing CVE ID.

## What to expect

| Stage | Target SLA |
|---|---|
| Acknowledgement of your report | 2 business days |
| Initial assessment + severity triage | 5 business days |
| Patch availability for confirmed high/critical | 14 days |
| Patch availability for confirmed medium/low | 90 days |
| Coordinated public disclosure | Defaults: 90 days standard, 14 days critical |

We use [CVSS v4.0](https://www.first.org/cvss/v4-0/) for severity scoring.

## Coordinated disclosure

PatchWright's defaults (configurable in `patchwright.yaml` per case):

- **Standard embargo:** 90 days from acknowledgement.
- **Critical embargo:** 14 days from acknowledgement.
- **Public disclosure** is coordinated with the reporter. We credit the reporter unless they request anonymity.
- **CVE filing** is via MITRE CVE Services when PatchWright is the affected component; we do not request CVE IDs for issues in third-party software (PatchWright is not a CNA for anyone else's project).

## Safe harbor

We will not pursue legal action against you for good-faith security research that:

- Stays within the scope of the PatchWright source tree, official releases, and explicitly designated test infrastructure.
- Does not access, modify, or exfiltrate data belonging to third parties.
- Does not degrade availability for users (no DoS or load testing without prior agreement).
- Reports findings through the channels above before public disclosure (subject to the timelines above).

## Supported versions

PatchWright is pre-alpha (Phase 0). There are no released versions yet. When releases begin, only the latest minor release receives security backports until 1.0; from 1.0 we will publish a supported-versions matrix here.

## Hall of fame

We publicly thank researchers who responsibly disclose issues at `https://patchwright.dev/security/credits` (will go live with the first release).

## Cryptographic provenance

Releases (when they exist) will be:

- Built reproducibly.
- Published with [SLSA Level 3](https://slsa.dev/) attestations.
- Signed with [Sigstore cosign](https://www.sigstore.dev/) under the PatchWright project's Fulcio identity.

See [PRD §8](PRD.md#8-security--runtime-integrity-requirements-nfr-s-) for the full runtime-integrity requirements.
