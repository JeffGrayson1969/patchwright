class PatchWrightError(Exception):
    """Base class for all PatchWright runtime errors."""


class IllegalTransition(PatchWrightError):
    """An agent proposed a transition not allowed by the FSM from the current state."""


class StaleAgent(PatchWrightError):
    """An agent proposed a transition whose from_state does not match the current Case state."""


class JournalCorrupt(PatchWrightError):
    """The journal could not be parsed or its invariants failed."""


class ChainBroken(JournalCorrupt):
    """An entry's prev_hash does not match the previous entry's content_hash."""


class ArtifactMissing(PatchWrightError):
    """A journal entry references an artifact sha that is not in the store."""
