# Writing a PatchWright plugin

PatchWright's adapter boundaries are plugins (PRD §10.1 #7): intake adapters,
agents, and LLM providers are all discovered through Python
[entry points](https://packaging.python.org/en/latest/specifications/entry-points/).
A plugin is just a normal Python distribution that declares one or more entry
points in the groups below.

> **Trust:** third-party plugins are **refused at load time unless trusted**
> (NFR-S-8). See [Trust store](#trust-store) before you expect your plugin to
> load in someone else's deployment.

## Entry-point groups

| Group | What it provides | Object shape |
| --- | --- | --- |
| `patchwright.plugins.agents` | An agent bound to one FSM state | object with `name: str`, `handles_state: str`, `__call__(case, store) -> AgentResult` |
| `patchwright.plugins.llm_providers` | An `LLMProvider` | class satisfying `patchwright.core.llm.LLMProvider` |

Intake adapters implement the `IntakeAdapter` Protocol
(`patchwright.core.intake`) and are selected by `source` today; a dedicated
entry-point group lands with the intake plugin surface.

## Example: an agent plugin

`pyproject.toml` of your plugin distribution:

```toml
[project]
name = "patchwright-plugin-acme"
version = "0.1.0"

[project.entry-points."patchwright.plugins.agents"]
acme_triage = "patchwright_plugin_acme.triage:agent"
```

`patchwright_plugin_acme/triage.py`:

```python
from dataclasses import dataclass, field
from patchwright.core.fsm import State
from patchwright.core.models import AgentResult, Transition

@dataclass
class AcmeTriage:
    name: str = "acme_triage"
    handles_state: str = field(default=str(State.INTAKE))

    def __call__(self, case, store) -> AgentResult:
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.INTAKE),
                to_state=str(State.TRIAGED),
                reason="acme",
            ),
            reason="acme",
        )

agent = AcmeTriage()
```

An LLM provider plugin is the same pattern against
`patchwright.plugins.llm_providers`, exporting a class that implements
`complete(*, system, user, response_schema=None, max_output_tokens=...)`.

## Trust store

`Registry.load_entry_points()` enforces a trust policy
(`patchwright.core.plugins.PluginPolicy`, config section `plugins`):

- **First-party** plugins (shipped in the `patchwright` distribution) always load.
- Any other plugin is **refused** (skipped with a warning) unless its
  distribution name is listed in `plugins.trusted`, or `plugins.allow_unsigned`
  is set.

```yaml
# patchwright.yaml
plugins:
  allow_unsigned: false          # default: refuse untrusted plugins
  trusted:
    - patchwright-plugin-acme     # PEP 503 name; case/dash/underscore-insensitive
```

`allow_unsigned: true` loads every discovered plugin regardless of trust — for
local development only. **Never** enable it in a deployment that handles
embargoed reports (T8).

## Signing plugin artifacts (cosign)

The trust store is the *load-time* gate. Cryptographic assurance happens at
*distribution time*: sign your wheel + sdist with
[cosign](https://docs.sigstore.dev/) keyless so operators can verify provenance
before installing (this mirrors how first-party PatchWright releases are signed —
see `.github/workflows/release.yml`).

```bash
# in your plugin's release workflow (keyless, GitHub OIDC — no stored key)
cosign sign-blob --yes \
  --output-signature dist/pkg.whl.sig \
  --output-certificate dist/pkg.whl.crt \
  dist/pkg.whl
```

Operators verify before installing and adding you to `plugins.trusted`:

```bash
cosign verify-blob \
  --certificate dist/pkg.whl.crt \
  --signature dist/pkg.whl.sig \
  --certificate-identity-regexp '^https://github.com/<you>/.+' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  dist/pkg.whl
```
