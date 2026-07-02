"""MCP server + tool layer (AEG-379, M7).

Exercises the tool functions directly (offline, no LLM/docker) plus the FastMCP
catalog. LLM/sandbox-backed steps are covered for their structured-error and
no-op behaviors; full drive-to-AWAITING_REVIEW needs live services (gated).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from patchwright.core.config import PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.llm import LLMConfigError
from patchwright.mcp_server import tools
from patchwright.mcp_server.server import TOOL_NAMES, build_server

_GHSA_FIXTURE = Path(__file__).parent / "fixtures" / "intake" / "sample_ghsa.json"


def _cfg() -> PatchwrightConfig:
    return PatchwrightConfig()


def _open_case(root: Path) -> str:
    result = tools.intake_report(
        root=root, config=_cfg(), raw=_GHSA_FIXTURE.read_text(), source="ghsa"
    )
    assert result["ok"]
    return result["case_id"]


# --------------------------------------------------------------------------- catalog


def test_server_exposes_eight_tools(tmp_path: Path) -> None:
    server = build_server(tmp_path, _cfg())
    listed = asyncio.run(server.list_tools())
    names = {t.name for t in listed}
    assert names == set(TOOL_NAMES)
    assert len(TOOL_NAMES) == 8
    # Every tool has a description and an input schema.
    for t in listed:
        assert t.description
        assert t.inputSchema is not None


# --------------------------------------------------------------------------- pure tools


def test_intake_report_opens_case(tmp_path: Path) -> None:
    result = tools.intake_report(
        root=tmp_path, config=_cfg(), raw=_GHSA_FIXTURE.read_text(), source="ghsa"
    )
    assert result["ok"]
    assert result["state"] == str(State.INTAKE)
    assert "raw_report" in result["artifacts"]


def test_intake_report_bad_source_errors(tmp_path: Path) -> None:
    result = tools.intake_report(root=tmp_path, config=_cfg(), raw="{}", source="nope")
    assert result["ok"] is False
    assert "error" in result


def test_get_status_single_and_list(tmp_path: Path) -> None:
    case_id = _open_case(tmp_path)

    one = tools.get_status(root=tmp_path, config=_cfg(), case_id=case_id)
    assert one["ok"] and one["case_id"] == case_id and one["state"] == str(State.INTAKE)

    listing = tools.get_status(root=tmp_path, config=_cfg())
    assert listing["ok"]
    assert any(c["case_id"] == case_id for c in listing["cases"])


def test_get_status_missing_case(tmp_path: Path) -> None:
    result = tools.get_status(root=tmp_path, config=_cfg(), case_id="case-doesnotexist")
    assert result["ok"] is False


def test_explain_case_returns_markdown(tmp_path: Path) -> None:
    case_id = _open_case(tmp_path)
    result = tools.explain_case(root=tmp_path, config=_cfg(), case_id=case_id)
    assert result["ok"]
    assert case_id in result["markdown"]


# --------------------------------------------------------------------------- agent tools


def test_reproduce_on_intake_case_is_noop(tmp_path: Path) -> None:
    """ReproduceAgent handles TRIAGED; on an INTAKE case drive() finds no agent
    and pauses — a deterministic no-op, no docker needed."""
    case_id = _open_case(tmp_path)
    result = tools.reproduce_poc(root=tmp_path, config=_cfg(), case_id=case_id)
    assert result["ok"]
    assert result["state"] == str(State.INTAKE)


def test_triage_without_provider_returns_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = _open_case(tmp_path)

    def boom(_config: object) -> object:
        raise LLMConfigError("no key")

    monkeypatch.setattr("patchwright.providers.factory.provider_from_config", boom)
    result = tools.triage_case(root=tmp_path, config=_cfg(), case_id=case_id)
    assert result["ok"] is False
    assert "provider" in result["error"].lower()


# --------------------------------------------------------------------------- deferred tools


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", "x\\y", ""])
def test_traversal_case_id_rejected(tmp_path: Path, bad: str) -> None:
    """A prompt-injected host cannot escape the persistence root via case_id."""
    for fn in (tools.explain_case, tools.triage_case, tools.reproduce_poc):
        result = fn(root=tmp_path, config=_cfg(), case_id=bad)
        assert result["ok"] is False
        assert "invalid case_id" in result["error"]
    if bad:  # empty case_id is "list mode" for get_status, not a lookup
        status = tools.get_status(root=tmp_path, config=_cfg(), case_id=bad)
        assert status["ok"] is False


def test_apply_patch_is_deferred(tmp_path: Path) -> None:
    result = tools.apply_patch(root=tmp_path, config=_cfg(), case_id="case-x")
    assert result["ok"] is False
    assert result["status"] == "not_wired"


def test_draft_advisory_is_p2(tmp_path: Path) -> None:
    result = tools.draft_advisory(root=tmp_path, config=_cfg(), case_id="case-x")
    assert result["ok"] is False
    assert result["status"] == "p2"
