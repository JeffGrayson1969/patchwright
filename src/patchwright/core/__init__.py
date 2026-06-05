"""PatchWright core: FSM orchestrator + append-only journal + content-addressed artifacts."""

from patchwright.core.config import (
    CONFIG_FILENAME,
    ConfigError,
    ConventionsConfig,
    EmbargoConfig,
    LLMConfig,
    PatchwrightConfig,
    ReviewConfig,
    SandboxConfig,
)
from patchwright.core.errors import (
    ChainBroken,
    IllegalTransition,
    JournalCorrupt,
    StaleAgent,
)
from patchwright.core.fsm import (
    INITIAL_STATE,
    TERMINAL_STATES,
    State,
    is_legal,
    legal_targets,
)
from patchwright.core.hashing import GENESIS_HASH, canonical_json, sha256_b16
from patchwright.core.models import (
    AgentResult,
    Artifact,
    Case,
    JournalEntry,
    Transition,
)

__all__ = [
    "CONFIG_FILENAME",
    "GENESIS_HASH",
    "INITIAL_STATE",
    "TERMINAL_STATES",
    "AgentResult",
    "Artifact",
    "Case",
    "ChainBroken",
    "ConfigError",
    "ConventionsConfig",
    "EmbargoConfig",
    "IllegalTransition",
    "JournalCorrupt",
    "JournalEntry",
    "LLMConfig",
    "PatchwrightConfig",
    "ReviewConfig",
    "SandboxConfig",
    "StaleAgent",
    "State",
    "Transition",
    "canonical_json",
    "is_legal",
    "legal_targets",
    "sha256_b16",
]
