"""Plugin trust policy + load-time enforcement (AEG-378, FR-CF-2, NFR-S-8)."""

from __future__ import annotations

from dataclasses import dataclass

from patchwright.core.config import PatchwrightConfig, PluginConfig
from patchwright.core.fsm import State
from patchwright.core.plugins import PluginPolicy
from patchwright.core.registry import Registry

# --------------------------------------------------------------------------- fakes


@dataclass
class _FakeAgent:
    name: str
    handles_state: str


@dataclass
class _FakeDist:
    name: str


@dataclass
class _FakeEntryPoint:
    name: str
    obj: object
    dist: _FakeDist | None

    def load(self) -> object:
        return self.obj


def _ep(name: str, dist: str | None, state: str) -> _FakeEntryPoint:
    return _FakeEntryPoint(
        name=name,
        obj=_FakeAgent(name=name, handles_state=state),
        dist=_FakeDist(dist) if dist is not None else None,
    )


# --------------------------------------------------------------------------- policy


def test_first_party_always_trusted() -> None:
    assert PluginPolicy.default().allows("patchwright")


def test_untrusted_refused_by_default() -> None:
    assert not PluginPolicy.default().allows("evil-plugin")


def test_none_dist_is_untrusted() -> None:
    assert not PluginPolicy.default().allows(None)


def test_trusted_list_allows() -> None:
    policy = PluginPolicy(trusted=frozenset({"acme-plugin"}))
    assert policy.allows("acme-plugin")
    assert not policy.allows("other-plugin")


def test_trust_matching_is_pep503_normalized() -> None:
    policy = PluginPolicy(trusted=frozenset({"Acme_Plugin"}))
    assert policy.allows("acme-plugin")  # underscore/case/dash-insensitive


def test_allow_unsigned_permits_everything() -> None:
    policy = PluginPolicy(allow_unsigned=True)
    assert policy.allows("evil-plugin")
    assert policy.allows(None)


def test_from_config() -> None:
    config = PatchwrightConfig(plugins=PluginConfig(allow_unsigned=False, trusted=["acme"]))
    policy = PluginPolicy.from_config(config)
    assert policy.allows("acme")
    assert not policy.allows("nope")


# --------------------------------------------------------------------------- enforcement


def test_load_refuses_untrusted_plugin() -> None:
    registry = Registry()
    eps = [
        _ep("builtin", "patchwright", str(State.INTAKE)),
        _ep("evil", "evil-plugin", str(State.TRIAGED)),
    ]
    loaded = registry.load_entry_points(entry_points=eps)  # default policy: first-party only
    assert loaded == ["builtin"]
    assert registry.agent_for_state(str(State.INTAKE)) is not None
    assert registry.agent_for_state(str(State.TRIAGED)) is None  # untrusted refused


def test_load_allows_trusted_plugin() -> None:
    registry = Registry()
    eps = [_ep("acme", "acme-plugin", str(State.TRIAGED))]
    policy = PluginPolicy(trusted=frozenset({"acme-plugin"}))
    loaded = registry.load_entry_points(entry_points=eps, policy=policy)
    assert loaded == ["acme"]
    assert registry.agent_for_state(str(State.TRIAGED)) is not None


def test_load_allow_unsigned_loads_all() -> None:
    registry = Registry()
    eps = [
        _ep("a", "one-plugin", str(State.INTAKE)),
        _ep("b", None, str(State.TRIAGED)),
    ]
    loaded = registry.load_entry_points(entry_points=eps, policy=PluginPolicy(allow_unsigned=True))
    assert set(loaded) == {"a", "b"}


def test_empty_plugins_config_is_secure_default() -> None:
    assert PatchwrightConfig().plugins.allow_unsigned is False
    assert PatchwrightConfig().plugins.trusted == []
