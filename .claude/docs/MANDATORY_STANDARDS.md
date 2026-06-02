# Mandatory Standards — All Agent Teams

**EVERY agent on EVERY team MUST follow these standards. No exceptions.**

## 1. Unit Tests Required

Every code change MUST include unit tests:
- **New feature:** Test the happy path + at least 2 edge cases
- **Bug fix:** Test that reproduces the bug (fails before fix, passes after)
- **Security fix:** Test the attack vector is blocked + test bypass variations
- **Refactor:** Existing tests still pass + new tests for changed behavior

**No PR/fix is complete without tests.** If you're unsure what to test, ask the QA Team Lead.

**Test naming convention:** `test_<what>_<condition>_<expected>`
```
test_login_invalid_credentials_returns_401
test_tenant_query_wrong_tenant_returns_403
test_sql_injection_payload_blocked
```

## 2. Version Numbers Required

Every file you modify that has a version number: **bump it.**

- **CLAUDE.md:** Update the version table at the top
- **package.json / pyproject.toml:** Bump version per semver
- **Service files:** Update version comments/constants
- **API specs (openapi.yaml):** Bump the info.version field
- **Helm charts (Chart.yaml):** Bump chart version

**Semver rules:**
- PATCH (0.0.X) — bug fix, security fix, dependency update
- MINOR (0.X.0) — new feature, new endpoint, new capability
- MAJOR (X.0.0) — breaking API change, architecture change

## 3. Dates Required

Every significant change MUST be dated:

- **CLAUDE.md version table:** Add a row with the date and description
- **Changelog comments:** Include date in format `YYYY-MM-DD`
- **Script headers:** Update `# Last Updated: YYYY-MM-DD`
- **Documentation:** Update "Last Updated" or "Date" fields

**Use ISO 8601 format: `2026-03-31`**

## 4. Documentation Updates Required

When you change code, update ALL affected documentation:

### Always Update:
- **CLAUDE.md** — version history table, any affected sections
- **README.md** — if the change affects setup, usage, or architecture
- **Inline comments** — update comments near changed code
- **API docs** — if endpoints changed (openapi.yaml, API_DOCUMENTATION.md)

### Update If Affected:
- **Runbooks** — if operational procedures changed
- **Troubleshooting guides** — if new failure modes or fixes
- **User guides** — if user-facing behavior changed
- **Architecture docs** — if services, data flow, or dependencies changed
- **Deployment docs** — if deployment steps, configs, or environments changed

### Never Leave Stale:
- **Scripts** — if a script's behavior changes, update its `--help` output and README
- **Config files** — if defaults change, update example configs
- **Environment variables** — if new env vars added, update .env.example and deployment docs
- **Linear issues** — always update the issue with what was done, when, and which version

## 5. Linear Issue Hygiene

Every change MUST reference a Linear issue:
- **Before starting:** Move issue to "In Progress"
- **After completing:** Add a comment with: what changed, which files, version bumped, tests added
- **If blocked:** Add a comment explaining the blocker, tag the relevant team lead
- **When done:** Move to "Done" (after QA + Delta sign-off)

## 6. Commit Message Standards

```
[AEG-XX] Brief description of change

- What was changed and why
- Files modified
- Tests added/modified
- Version bumped to X.Y.Z
```

## 7. Cross-Team Communication

When your change affects another team's code or workflows:
- **Tag the other team's lead** in the Linear issue
- **Add a note in CLAUDE.md** about the cross-team impact
- **Notify the PM** if it's a blocker or dependency

## Summary Checklist

Before marking any task complete, verify:

- [ ] Unit tests written and passing
- [ ] Version number bumped (semver)
- [ ] Date added to CLAUDE.md version table
- [ ] All affected documentation updated
- [ ] Linear issue updated with details
- [ ] No stale comments, scripts, or configs left behind
