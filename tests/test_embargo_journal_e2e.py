"""End-to-end embargoed-case journal encryption via orchestrator + CLI (AEG-376).

Exit criterion (AEG-375/AEG-376): `patchwright journal --case <id>` requires the
operator key to decrypt embargoed-case entries.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from patchwright.cli.__main__ import main as cli_main
from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.config import EmbargoConfig, PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.intake import ingest
from patchwright.core.journal import Journal
from patchwright.core.journal_crypto import JournalCipher, generate_key_b64
from patchwright.core.models import AgentResult, Case, Transition
from patchwright.core.orchestrator import case_root_paths, drive, open_case
from patchwright.core.registry import Registry
from patchwright.core.secrets import SecretNotFound

KEY_B64 = generate_key_b64()
CASE_ID = "case-embargoe2e1"
_GHSA_FIXTURE = Path(__file__).parent / "fixtures" / "intake" / "sample_ghsa.json"


def _with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("patchwright.core.journal_crypto.get_secret", lambda *a, **k: KEY_B64)


def _without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(key: str, *, required: bool = True) -> str | None:
        if required:
            raise SecretNotFound(key)
        return None

    monkeypatch.setattr("patchwright.core.journal_crypto.get_secret", fake)


@dataclass
class _StubReject:
    """INTAKE -> REJECTED (terminal) so drive() writes one transition then stops."""

    name: str = "stub"
    handles_state: str = field(default=str(State.INTAKE))

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.INTAKE),
                to_state=str(State.REJECTED),
                reason="stub",
            ),
            reason="stub",
        )


def _strict() -> PatchwrightConfig:
    return PatchwrightConfig(embargo=EmbargoConfig(mode="strict"))


def test_strict_mode_encrypts_and_replays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_key(monkeypatch)
    root = tmp_path / "cases"

    open_case(case_id=CASE_ID, root=root, raw_report=b'{"x":1}', config=_strict())
    registry = Registry()
    registry.register(_StubReject())
    final = drive(CASE_ID, registry, root, config=_strict())
    assert final.state == str(State.REJECTED)

    # On-disk journal is ciphertext: no state names / kinds leak.
    raw = (case_root_paths(root, CASE_ID)["journal_dir"] / Journal.JOURNAL_FILENAME).read_bytes()
    assert b"pw_enc" in raw
    assert b"case_opened" not in raw
    assert b"REJECTED" not in raw

    # Replays cleanly with the key.
    cipher = JournalCipher(base64.b64decode(KEY_B64))
    entries = Journal(case_root_paths(root, CASE_ID)["journal_dir"], cipher=cipher).read()
    assert [e.kind for e in entries] == ["case_opened", "transition", "case_closed"]


def test_strict_mode_without_key_refuses_to_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_key(monkeypatch)
    with pytest.raises(SecretNotFound):
        open_case(case_id="case-nokey01", root=tmp_path, raw_report=b"{}", config=_strict())


def test_cli_journal_requires_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cases"

    # Write an embargoed case with the key.
    _with_key(monkeypatch)
    open_case(case_id=CASE_ID, root=root, raw_report=b'{"x":1}', config=_strict())

    # With the key: command succeeds and prints the decrypted entries.
    rc = cli_main(["journal", "--case", CASE_ID, "--root", str(root)])
    assert rc == 0

    # Without the key: command refuses (exit 2), does not leak plaintext.
    _without_key(monkeypatch)
    rc_nokey = cli_main(["journal", "--case", CASE_ID, "--root", str(root)])
    assert rc_nokey == 2


def test_ingest_strict_encrypts_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (T4): the primary intake path must encrypt in strict mode —
    not just open_case/drive. Guards against ingest() dropping config."""
    _with_key(monkeypatch)
    case = ingest(_GHSA_FIXTURE.read_bytes(), source="ghsa", root=tmp_path, config=_strict())

    raw = (
        case_root_paths(tmp_path, case.id)["journal_dir"] / Journal.JOURNAL_FILENAME
    ).read_bytes()
    assert b"pw_enc" in raw
    assert b"case_opened" not in raw
    assert b"raw_report" not in raw  # artifact-kind metadata is inside the encrypted entry


def test_ingest_strict_without_key_hard_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T4: strict intake with no operator key must refuse, not fall back to plaintext."""
    _without_key(monkeypatch)
    with pytest.raises(SecretNotFound):
        ingest(_GHSA_FIXTURE.read_bytes(), source="ghsa", root=tmp_path, config=_strict())


def test_cli_journal_prints_entries_with_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "cases"
    _with_key(monkeypatch)
    open_case(case_id=CASE_ID, root=root, raw_report=b'{"x":1}', config=_strict())

    capsys.readouterr()  # clear
    rc = cli_main(["journal", "--case", CASE_ID, "--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "case_opened" in out  # decrypted content is visible in stdout
