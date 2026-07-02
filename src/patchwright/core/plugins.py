"""Plugin trust policy (FR-CF-2, NFR-S-8, T8).

Runtime enforcement for third-party entry-point plugins: first-party plugins
(the `patchwright` distribution) are always trusted; any other plugin is refused
at load time unless its distribution is in the operator's trust store — or
`allow_unsigned` is set. This is the load-time gate; cryptographic signing of
plugin *artifacts* is a distribution-time concern handled by cosign (release.yml
for first-party; docs/plugins.md for plugin authors).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchwright.core.config import PatchwrightConfig

FIRST_PARTY_DIST = "patchwright"


def _normalize(name: str) -> str:
    """PEP 503 normalization so 'Patch_Wright' == 'patch-wright'."""
    return re.sub(r"[-_.]+", "-", name).lower().strip("-")


@dataclass(frozen=True)
class PluginPolicy:
    """Decides whether a plugin from a given distribution may load."""

    allow_unsigned: bool = False
    trusted: frozenset[str] = frozenset()
    first_party: str = FIRST_PARTY_DIST

    def allows(self, dist_name: str | None) -> bool:
        """True iff a plugin from `dist_name` is permitted to load.

        `dist_name` is None when the distribution can't be determined — that is
        untrusted (fail closed) unless allow_unsigned."""
        if self.allow_unsigned:
            return True
        if dist_name is None:
            return False
        normalized = _normalize(dist_name)
        return normalized == _normalize(self.first_party) or normalized in self._trusted_normalized

    @property
    def _trusted_normalized(self) -> frozenset[str]:
        return frozenset(_normalize(t) for t in self.trusted)

    @classmethod
    def from_config(cls, config: PatchwrightConfig) -> PluginPolicy:
        return cls(
            allow_unsigned=config.plugins.allow_unsigned,
            trusted=frozenset(config.plugins.trusted),
        )

    @classmethod
    def default(cls) -> PluginPolicy:
        """Secure default: only first-party plugins load."""
        return cls()


__all__ = ["FIRST_PARTY_DIST", "PluginPolicy"]
