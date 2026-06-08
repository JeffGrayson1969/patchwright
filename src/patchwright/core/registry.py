from __future__ import annotations

import importlib.metadata
import logging

from patchwright.core.protocols import Agent

log = logging.getLogger(__name__)

AGENT_ENTRY_POINT_GROUP = "patchwright.plugins.agents"


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
    ) -> None:
        """Load all installed entry points in the given group."""
        try:
            eps = importlib.metadata.entry_points(group=group)
        except Exception:  # pragma: no cover - very old Python only
            return
        for ep in eps:
            try:
                obj = ep.load()
            except Exception as exc:  # pragma: no cover - skip broken plugin
                log.warning("failed to load entry point %s: %s", ep.name, exc)
                continue
            self.register(obj)


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
