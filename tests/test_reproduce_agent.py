"""reproduce agent — fake SandboxRunner, pre-seeded poc_spec artifacts (AEG-462).

Covers the routing matrix:
  - no poc_spec on case          -> NOT_REPRODUCIBLE (no_poc)
  - sandbox exit 0               -> REPRODUCED
  - sandbox exit non-zero        -> NOT_REPRODUCIBLE (poc_did_not_trigger)
  - sandbox timed_out            -> NOT_REPRODUCIBLE (timeout)
  - sandbox raises SandboxError  -> NOT_REPRODUCIBLE (sandbox_unavailable)

Plus structural checks:
  - Agent satisfies the Agent Protocol
  - handles_state is TRIAGED
  - PocSpec.image override honored; falls back to config.conventions.repro_image
  - script blob materialized to <case_root>/scratch/<case>/poc/poc.sh, mounted RO at /poc
  - No script -> no mounts
  - Latest poc_spec wins when multiple exist on the case
  - stdio tail trimmed at 4 KiB each
  - poc_artifact_id carried into repro_log
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchwright.agents.reproduce import ReproduceAgent
from patchwright.core.artifacts import ArtifactStore, ReadOnlyArtifactStore
from patchwright.core.config import ConventionsConfig, PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.models import Artifact, Case
from patchwright.core.protocols import Agent
from patchwright.core.sandbox import (
    Mount,
    NetworkPolicy,
    ResourceLimits,
    RunResult,
    SandboxError,
)
from patchwright.models.poc import PocSpec
from patchwright.models.repro import ReproLog

# --------------------------------------------------------------------------- fakes


@dataclass
class FakeSandbox:
    """Records the last run() call; returns next_result or raises next_error."""

    next_result: RunResult | None = None
    next_error: SandboxError | None = None
    last_call: dict[str, Any] = field(default_factory=dict)
    name: str = "fake"

    def is_available(self) -> bool:
        return True

    def run(
        self,
        *,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        network_policy: NetworkPolicy | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> RunResult:
        self.last_call = {
            "image": image,
            "cmd": cmd,
            "mounts": mounts or [],
            "env": env or {},
            "timeout": timeout,
        }
        if self.next_error is not None:
            raise self.next_error
        if self.next_result is None:
            raise RuntimeError("FakeSandbox has no next_result configured")
        return self.next_result


def _result(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    image: str = "alpine:3.20",
    cmd: tuple[str, ...] = ("true",),
) -> RunResult:
    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        image=image,
        cmd=cmd,
    )


# --------------------------------------------------------------------------- factories


def _make_spec(
    *,
    cmd: tuple[str, ...] = ("sh", "-c", "exit 0"),
    image: str | None = None,
    script: str | None = None,
    timeout_seconds: float = 30.0,
) -> PocSpec:
    return PocSpec(cmd=cmd, image=image, script=script, timeout_seconds=timeout_seconds)


def _make_case(
    *,
    tmp_path: Path,
    case_id: str = "case-abc123def456",
    spec: PocSpec | None = None,
) -> tuple[Case, ReadOnlyArtifactStore, ArtifactStore, str | None]:
    """Build a TRIAGED case with optional poc_spec artifact attached."""
    store = ArtifactStore(tmp_path / "artifacts")
    artifacts: list[Artifact] = []
    spec_id: str | None = None
    if spec is not None:
        spec_bytes = canonical_json(spec.model_dump(mode="json"))
        spec_id = store.put(spec_bytes)
        artifacts.append(Artifact(id=spec_id, kind="poc_spec", size=len(spec_bytes)))
    case = Case(
        id=case_id,
        state=str(State.TRIAGED),
        created_at="2026-06-19T00:00:00.000000Z",
        artifacts=artifacts,
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    return case, store.read_only(), store, spec_id


def _make_agent(
    *,
    tmp_path: Path,
    sandbox: FakeSandbox,
    config: PatchwrightConfig | None = None,
) -> ReproduceAgent:
    return ReproduceAgent(
        sandbox=sandbox,
        case_root=tmp_path / "case_root",
        config=config or PatchwrightConfig(),
    )


# --------------------------------------------------------------------------- structural


def test_agent_satisfies_protocol(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path=tmp_path, sandbox=FakeSandbox())
    assert isinstance(agent, Agent)


def test_agent_handles_triaged_state(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path=tmp_path, sandbox=FakeSandbox())
    assert agent.handles_state == str(State.TRIAGED)


def test_agent_name_is_reproduce(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path=tmp_path, sandbox=FakeSandbox())
    assert agent.name == "reproduce"


# --------------------------------------------------------------------------- no PoC


def test_no_poc_spec_routes_to_not_reproducible(tmp_path: Path) -> None:
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=None)
    sandbox = FakeSandbox()
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.from_state == str(State.TRIAGED)
    assert result.transition.to_state == str(State.NOT_REPRODUCIBLE)
    assert result.transition.reason == "no_poc"
    assert result.reason == "no_poc"
    assert sandbox.last_call == {}  # sandbox never invoked

    assert len(result.new_artifacts) == 1
    log_bytes, kind = result.new_artifacts[0]
    assert kind == "repro_log"
    log = ReproLog.model_validate_json(log_bytes)
    assert log.case_id == case.id
    assert log.verdict == "not_reproducible"
    assert log.poc_artifact_id is None
    assert log.cmd == ()
    assert log.exit_code == -1
    assert log.image == PatchwrightConfig().conventions.repro_image
    assert log.sandbox_name == "fake"


# --------------------------------------------------------------------------- happy path


def test_exit_zero_routes_to_reproduced(tmp_path: Path) -> None:
    spec = _make_spec(cmd=("sh", "-c", "exit 0"))
    case, ro_store, _, spec_id = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result(exit_code=0, stdout="boom\n"))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.from_state == str(State.TRIAGED)
    assert result.transition.to_state == str(State.REPRODUCED)
    assert result.transition.reason == "reproduced"
    assert result.reason == "reproduced"

    log_bytes, kind = result.new_artifacts[0]
    assert kind == "repro_log"
    log = ReproLog.model_validate_json(log_bytes)
    assert log.verdict == "reproduced"
    assert log.exit_code == 0
    assert log.poc_artifact_id == spec_id
    assert log.cmd == ("sh", "-c", "exit 0")
    assert log.stdout_tail == "boom\n"
    assert log.timed_out is False


# --------------------------------------------------------------------------- not-reproducible


def test_non_zero_exit_routes_to_not_reproducible(tmp_path: Path) -> None:
    spec = _make_spec(cmd=("sh", "-c", "exit 7"))
    case, ro_store, _, spec_id = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result(exit_code=7, stderr="nope\n"))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.NOT_REPRODUCIBLE)
    assert result.transition.reason == "poc_did_not_trigger"
    assert result.reason == "poc_did_not_trigger"

    log = ReproLog.model_validate_json(result.new_artifacts[0][0])
    assert log.verdict == "not_reproducible"
    assert log.exit_code == 7
    assert log.poc_artifact_id == spec_id
    assert log.stderr_tail == "nope\n"


def test_timeout_routes_to_not_reproducible(tmp_path: Path) -> None:
    spec = _make_spec(cmd=("sh", "-c", "sleep 9999"))
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result(exit_code=-1, timed_out=True))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.NOT_REPRODUCIBLE)
    assert result.transition.reason == "timeout"
    log = ReproLog.model_validate_json(result.new_artifacts[0][0])
    assert log.timed_out is True
    assert log.verdict == "not_reproducible"


def test_sandbox_error_routes_to_not_reproducible(tmp_path: Path) -> None:
    spec = _make_spec()
    case, ro_store, _, spec_id = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_error=SandboxError("image not found"))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.NOT_REPRODUCIBLE)
    assert result.transition.reason == "sandbox_unavailable"
    log = ReproLog.model_validate_json(result.new_artifacts[0][0])
    assert log.verdict == "not_reproducible"
    assert "image not found" in log.reason
    # poc_artifact_id is still recorded — operator can re-drive once infra heals
    assert log.poc_artifact_id == spec_id


# --------------------------------------------------------------------------- image resolution


def test_poc_spec_image_overrides_config_default(tmp_path: Path) -> None:
    spec = _make_spec(image="python:3.12-slim")
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result(image="python:3.12-slim"))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    agent(case, ro_store)

    assert sandbox.last_call["image"] == "python:3.12-slim"


def test_falls_back_to_config_repro_image_when_spec_image_unset(tmp_path: Path) -> None:
    spec = _make_spec(image=None)
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result())
    config = PatchwrightConfig(conventions=ConventionsConfig(repro_image="ubuntu:22.04"))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox, config=config)

    agent(case, ro_store)

    assert sandbox.last_call["image"] == "ubuntu:22.04"


# --------------------------------------------------------------------------- script mount


def test_script_materialized_and_mounted_readonly(tmp_path: Path) -> None:
    spec = _make_spec(
        cmd=("sh", "/poc/poc.sh"),
        script="#!/bin/sh\nexit 0\n",
    )
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result(exit_code=0))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    agent(case, ro_store)

    poc_dir = tmp_path / "case_root" / "scratch" / case.id / "poc"
    poc_script = poc_dir / "poc.sh"
    assert poc_script.exists()
    assert poc_script.read_text() == "#!/bin/sh\nexit 0\n"

    mounts = sandbox.last_call["mounts"]
    assert len(mounts) == 1
    assert mounts[0].source == poc_dir.resolve()
    assert mounts[0].target == "/poc"
    assert mounts[0].readonly is True


def test_no_script_means_no_mounts(tmp_path: Path) -> None:
    spec = _make_spec(script=None)
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result())
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    agent(case, ro_store)

    assert sandbox.last_call["mounts"] == []


def test_sandbox_timeout_passed_through_from_spec(tmp_path: Path) -> None:
    spec = _make_spec(timeout_seconds=15.0)
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result())
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    agent(case, ro_store)

    assert sandbox.last_call["timeout"] == 15.0


# --------------------------------------------------------------------------- artifact selection


def test_latest_poc_spec_artifact_wins(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    older = _make_spec(cmd=("echo", "old"))
    newer = _make_spec(cmd=("echo", "new"))

    older_bytes = canonical_json(older.model_dump(mode="json"))
    newer_bytes = canonical_json(newer.model_dump(mode="json"))
    older_id = store.put(older_bytes)
    newer_id = store.put(newer_bytes)

    case = Case(
        id="case-xy",
        state=str(State.TRIAGED),
        created_at="2026-06-19T00:00:00.000000Z",
        artifacts=[
            Artifact(id=older_id, kind="poc_spec", size=len(older_bytes)),
            Artifact(id=newer_id, kind="poc_spec", size=len(newer_bytes)),
        ],
        last_seq=1,
        last_hash="sha256:" + "0" * 64,
    )

    sandbox = FakeSandbox(next_result=_result())
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)
    result = agent(case, store.read_only())

    assert sandbox.last_call["cmd"] == ["echo", "new"]
    log = ReproLog.model_validate_json(result.new_artifacts[0][0])
    assert log.poc_artifact_id == newer_id


# --------------------------------------------------------------------------- stdio trimming


def test_stdio_tail_trimmed_at_4_kib(tmp_path: Path) -> None:
    big = "x" * 10_000
    spec = _make_spec()
    case, ro_store, _, _ = _make_case(tmp_path=tmp_path, spec=spec)
    sandbox = FakeSandbox(next_result=_result(stdout=big, stderr=big, exit_code=0))
    agent = _make_agent(tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    log = ReproLog.model_validate_json(result.new_artifacts[0][0])
    assert len(log.stdout_tail) == 4096
    assert len(log.stderr_tail) == 4096
    assert log.stdout_tail == "x" * 4096
