"""Regression: secrets never appear in journal entries.

Even if an API key is in the agent's call path, journal entries (and their
artifacts) must not contain its value. This is the load-bearing test for
NFR-S-10 plus the rule that providers do not echo credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from patchwright.agents.noop_closer import agent as noop_closer
from patchwright.agents.triage import TriageAgent
from patchwright.core.journal import Journal
from patchwright.core.orchestrator import case_root_paths, drive, open_case
from patchwright.core.registry import Registry
from patchwright.models.triage import TriageDisposition, TriagePacket

SECRET = "sk-test-DO-NOT-LEAK-1234567890"


@dataclass
class SecretAwareFakeLLM:
    """A provider whose constructor receives a secret. Verifies the secret
    never appears in any journal entry or persisted artifact, even though it
    sits on the provider instance."""

    api_key: str
    name: str = "secret-aware"
    model: str = "fake"

    def complete(self, **kwargs: Any) -> Any:
        # Sanity: secret is accessible to the provider
        assert self.api_key == SECRET
        return TriagePacket(
            case_id=kwargs["user"].split("\n")[0].split(": ")[1],
            summary="x",
            claim_type="x",
            confidence=0.5,
            disposition=TriageDisposition.ADVANCE,
            rationale="x",
        )


def test_secret_does_not_leak_into_journal(tmp_path: Path) -> None:
    case_id = "case-secret-leak-test"
    open_case(case_id=case_id, root=tmp_path, raw_report=b'{"id":"R"}')

    llm = SecretAwareFakeLLM(api_key=SECRET)
    registry = Registry()
    registry.register(TriageAgent(provider=llm))
    registry.register(noop_closer)

    drive(case_id, registry, tmp_path)

    # Scan every journal entry's raw bytes
    paths = case_root_paths(tmp_path, case_id)
    journal = Journal(paths["journal_dir"])
    raw = journal.path.read_bytes()
    assert SECRET.encode() not in raw

    # Scan every artifact in the global artifact dir
    artifacts_dir = paths["artifacts_dir"]
    for artifact_file in artifacts_dir.glob("*.bin"):
        assert SECRET.encode() not in artifact_file.read_bytes()
