"""Shared reproduce-agent fixtures (AEG-463).

Provides one real-CVE PoC — CVE-2007-4559, the Python `tarfile.extractall`
directory-traversal bug — packaged as a `PocSpec` the reproduce agent runs.
Consumed by the stub-sandbox e2e test and by the docker/gVisor integration
tests so they all share a single PoC corpus.
"""

from __future__ import annotations

from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.models import Artifact, Case
from patchwright.models.poc import PocSpec

_FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Pin 3.11: extractall still reproduces the traversal (default filter became
# 'data' only in 3.14), and 3.11 emits no DeprecationWarning noise.
CVE_2007_4559_IMAGE = "python:3.11-slim"
CVE_2007_4559_MARKER = "CVE-2007-4559"


def cve_2007_4559_poc_script() -> str:
    return (_FIXTURE_DIR / "cve_2007_4559_poc.sh").read_text(encoding="utf-8")


def cve_2007_4559_poc_spec(*, image: str | None = CVE_2007_4559_IMAGE) -> PocSpec:
    """PocSpec that reproduces CVE-2007-4559 inside a sandbox.

    `image=None` falls back to the agent's config default — used by the
    stub-sandbox e2e where nothing is actually pulled.
    """
    return PocSpec(
        image=image,
        cmd=("sh", "/poc/poc.sh"),
        script=cve_2007_4559_poc_script(),
        timeout_seconds=120.0,
    )


def triaged_case_with_poc(*, case_id: str, store: ArtifactStore, spec: PocSpec) -> Case:
    """A TRIAGED Case with `spec` attached as a poc_spec artifact, ready for the
    reproduce agent. Used by the docker/gVisor integration tests."""
    spec_bytes = canonical_json(spec.model_dump(mode="json"))
    spec_id = store.put(spec_bytes)
    return Case(
        id=case_id,
        state=str(State.TRIAGED),
        created_at="2026-06-19T00:00:00.000000Z",
        artifacts=[Artifact(id=spec_id, kind="poc_spec", size=len(spec_bytes))],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
