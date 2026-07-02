from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Iterable

from patchwright.core.plugins import PluginPolicy
from patchwright.core.protocols import Agent

log = logging.getLogger(__name__)

AGENT_ENTRY_POINT_GROUP = "patchwright.plugins.agents"


def _entry_point_dist_name(ep: importlib.metadata.EntryPoint) -> str | None:
    """Best-effort distribution name for an entry point (Python 3.10+ has ep.dist)."""
    dist = getattr(ep, "dist", None)
    name = getattr(dist, "name", None)
    return name if isinstance(name, str) else None


class Registry:
    """Plugin loader for agents. Backs onto Python entry points + explicit register()."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """Register an agent keyed by the state it handles. Last write wins."""
        if not hasattr(agent, "name") or not hasattr(agent, "handles_state"):
            raise TypeError(f"agent {agent!r} missing required attrs name/handles_state")
        self._agents[agent.handles_state] = agent

    def agent_for_state(self, state: str) -> Agent | None:
        return self._agents.get(state)

    def all(self) -> dict[str, Agent]:
        return dict(self._agents)

    def load_entry_points(
        self,
        group: str = AGENT_ENTRY_POINT_GROUP,
        *,
        policy: PluginPolicy | None = None,
        entry_points: Iterable[importlib.metadata.EntryPoint] | None = None,
    ) -> list[str]:
        """Load installed entry points in `group`, enforcing the plugin trust policy.

        Untrusted plugins (distribution not first-party and not in the operator's
        trust store, with allow_unsigned off) are refused at load time (NFR-S-8):
        skipped with a warning, never loaded. Returns the names actually loaded.

        `policy` defaults to the secure default (first-party only). `entry_points`
        is injectable for tests; otherwise discovered via importlib.metadata."""
        policy = policy or PluginPolicy.default()
        if entry_points is not None:
            eps: Iterable[importlib.metadata.EntryPoint] = entry_points
        else:
            try:
                eps = importlib.metadata.entry_points(group=group)
            except Exception:  # pragma: no cover - very old Python only
                return []

        loaded: list[str] = []
        for ep in eps:
            dist_name = _entry_point_dist_name(ep)
            if not policy.allows(dist_name):
                log.warning(
                    "refusing untrusted plugin %r (distribution %r not in trust store); "
                    "add it to plugins.trusted or set plugins.allow_unsigned",
                    ep.name,
                    dist_name,
                )
                continue
            try:
                obj = ep.load()
            except Exception as exc:  # pragma: no cover - skip broken plugin
                log.warning("failed to load entry point %s: %s", ep.name, exc)
                continue
            self.register(obj)
            loaded.append(ep.name)
        return loaded


def default_registry() -> Registry:
    """Registry with the P0 noop agents wired explicitly.

    Used by the `patchwright hello` demo and by tests that want a
    deterministic, LLM-free FSM walk. For real triage, use
    `triage_registry(provider)` instead.

    Entry-point discovery still works (see pyproject.toml), but tests and the
    hello demo do not depend on installed distribution metadata.
    """
    from patchwright.agents.noop_closer import agent as noop_closer  # noqa: PLC0415
    from patchwright.agents.noop_triage import agent as noop_triage  # noqa: PLC0415

    r = Registry()
    r.register(noop_triage)
    r.register(noop_closer)
    return r


def triage_registry(provider: object) -> Registry:
    """Registry wired with the real LLM-backed triage agent.

    The provider must satisfy patchwright.core.llm.LLMProvider. We accept
    `object` here to avoid an import-cycle into core/llm at module load.
    """
    from patchwright.agents.noop_closer import agent as noop_closer  # noqa: PLC0415
    from patchwright.agents.triage import TriageAgent  # noqa: PLC0415

    r = Registry()
    r.register(TriageAgent(provider=provider))  # type: ignore[arg-type]
    r.register(noop_closer)
    return r


def patch_plan_registry(provider: object, repo_root: object) -> Registry:
    """Registry wired for triage + patch-plan (M2-plan Wave B).

    Both provider and repo_root are typed as object to avoid import-cycle into
    core/llm and pathlib at module load time.
    """
    from pathlib import Path  # noqa: PLC0415

    from patchwright.agents.noop_closer import agent as noop_closer  # noqa: PLC0415
    from patchwright.agents.patch_plan import PatchPlanAgent  # noqa: PLC0415
    from patchwright.agents.triage import TriageAgent  # noqa: PLC0415

    r = Registry()
    r.register(TriageAgent(provider=provider))  # type: ignore[arg-type]
    r.register(PatchPlanAgent(provider=provider, repo_root=Path(str(repo_root))))  # type: ignore[arg-type]
    r.register(noop_closer)
    return r


def reproduce_registry(
    provider: object,
    sandbox: object,
    repo_root: object,
    case_root: object,
) -> Registry:
    """Registry wired for triage + patch-plan + reproduce (M3-hard.2, AEG-462).

    Adds the ReproduceAgent at the TRIAGED edge so `drive()` can take a case
    through INTAKE -> TRIAGED -> REPRODUCED | NOT_REPRODUCIBLE. provider drives
    triage + patch_plan; sandbox is any SandboxRunner (GVisorSandbox in prod,
    DockerSandbox in dev, stub in tests). All typed as object to avoid
    import-cycle at module load.
    """
    from pathlib import Path  # noqa: PLC0415

    from patchwright.agents.noop_closer import agent as noop_closer  # noqa: PLC0415
    from patchwright.agents.patch_plan import PatchPlanAgent  # noqa: PLC0415
    from patchwright.agents.reproduce import ReproduceAgent  # noqa: PLC0415
    from patchwright.agents.triage import TriageAgent  # noqa: PLC0415

    r = Registry()
    r.register(TriageAgent(provider=provider))  # type: ignore[arg-type]
    r.register(PatchPlanAgent(provider=provider, repo_root=Path(str(repo_root))))  # type: ignore[arg-type]
    r.register(ReproduceAgent(sandbox=sandbox, case_root=Path(str(case_root))))  # type: ignore[arg-type]
    r.register(noop_closer)
    return r


def cross_checker_registry(
    primary_provider: object,
    cross_checker_provider: object,
    repo_root: object,
) -> Registry:
    """Registry wired for triage + patch-plan + cross-checker (M2.5 Wave B).

    primary_provider drives triage + patch_plan; cross_checker_provider drives
    the cross-checker. Both typed as object to avoid import-cycle at module load.
    Build providers with provider_from_config / build_cross_checker from factory.py.
    """
    from pathlib import Path  # noqa: PLC0415

    from patchwright.agents.cross_checker import CrossCheckerAgent  # noqa: PLC0415
    from patchwright.agents.noop_closer import agent as noop_closer  # noqa: PLC0415
    from patchwright.agents.patch_plan import PatchPlanAgent  # noqa: PLC0415
    from patchwright.agents.triage import TriageAgent  # noqa: PLC0415

    r = Registry()
    r.register(TriageAgent(provider=primary_provider))  # type: ignore[arg-type]
    r.register(PatchPlanAgent(provider=primary_provider, repo_root=Path(str(repo_root))))  # type: ignore[arg-type]
    r.register(CrossCheckerAgent(provider=cross_checker_provider))  # type: ignore[arg-type]
    r.register(noop_closer)
    return r
