"""Pydantic models for agent inputs/outputs.

Distinct from patchwright.core.models (the journal/FSM data model). These are
the shapes agents emit and consume — TriagePacket, PatchPlan (P1+), etc.
"""

from patchwright.models.patch_plan import (
    AddTestCase,
    InsertImport,
    PatchOperation,
    PatchPlan,
    ReplaceFunctionBody,
    TestSpec,
    WrapCallWithValidator,
)
from patchwright.models.triage import TriageDisposition, TriagePacket

__all__ = [
    "AddTestCase",
    "InsertImport",
    "PatchOperation",
    "PatchPlan",
    "ReplaceFunctionBody",
    "TestSpec",
    "TriageDisposition",
    "TriagePacket",
    "WrapCallWithValidator",
]
