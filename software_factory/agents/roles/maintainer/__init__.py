"""The maintainer role: diagnoses CI failures and repairs the defects the pull request caused."""

from __future__ import annotations

from ... import _kit
from ...contract import ReplyMismatch, TypedAgent
from .messages import Diagnosis, DiagnoseRequest, Fixed, FixRequest

_CLASSIFICATIONS = ("branch", "transient", "blocked")
_VERDICTS = ("fixed", "blocked")

_PROFILE = _kit.profile_with_instructions(__name__, "maintainer", timeout_sec=1800)


def _reason(result: dict) -> str:
    reason = result.get("reason", "")
    if not isinstance(reason, str):
        raise ReplyMismatch(f"reason must be text: {result!r}")
    return reason


def _decode_diagnose(request: DiagnoseRequest, output: str) -> Diagnosis:
    result = _kit.read_object(output)
    if result.get("classification") not in _CLASSIFICATIONS:
        raise ReplyMismatch(f"invalid classification: {result.get('classification')!r}")
    return Diagnosis(result["classification"], _reason(result))


def _decode_fix(request: FixRequest, output: str) -> Fixed:
    result = _kit.read_object(output)
    if result.get("verdict") not in _VERDICTS:
        raise ReplyMismatch(f"invalid verdict: {result.get('verdict')!r}")
    return Fixed(result["verdict"], _reason(result))


DIAGNOSE_CI: TypedAgent[DiagnoseRequest, Diagnosis] = _kit.mode_agent("maintainer", "diagnose", profile=_PROFILE, decode=_decode_diagnose)
FIX_CI: TypedAgent[FixRequest, Fixed] = _kit.mode_agent("maintainer", "fix", profile=_PROFILE, decode=_decode_fix)

__all__ = ["DIAGNOSE_CI", "FIX_CI", "Diagnosis", "DiagnoseRequest", "Fixed", "FixRequest"]
