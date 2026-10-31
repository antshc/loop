"""The maintainer role: diagnoses CI failures and repairs the defects the pull request caused."""

from __future__ import annotations

from loop import ReplyMismatch, TypedAgent

from ... import _kit
from .messages import (
    ConflictRequest,
    DiagnoseRequest,
    Diagnosis,
    Fixed,
    FixRequest,
    JenkinsDiagnoseRequest,
    JenkinsDiagnosis,
)

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


def _decode_fix(request: FixRequest | ConflictRequest, output: str) -> Fixed:
    result = _kit.read_object(output)
    if result.get("verdict") not in _VERDICTS:
        raise ReplyMismatch(f"invalid verdict: {result.get('verdict')!r}")
    return Fixed(result["verdict"], _reason(result))


def _decode_jenkins(request: JenkinsDiagnoseRequest, output: str) -> JenkinsDiagnosis:
    result = _kit.read_object(output)
    classification, category = result.get("classification"), result.get("category")
    categories = {
        "timeout": "transient",
        "flaky_test": "transient",
        "container_error": "transient",
        "likely_transient": "transient",
        "test_failure": "branch",
        "branch_defect": "branch",
        "unknown": "blocked",
    }
    if (
        not isinstance(category, str)
        or category not in categories
        or categories[category] != classification
    ):
        raise ReplyMismatch("Jenkins category and classification are inconsistent")
    reason = _reason(result)
    if not reason.strip():
        raise ReplyMismatch("Jenkins diagnosis requires evidence-backed reasoning")
    return JenkinsDiagnosis(classification, category, reason)


DIAGNOSE_CI: TypedAgent[DiagnoseRequest, Diagnosis] = _kit.mode_agent(
    "maintainer", "diagnose", profile=_PROFILE, decode=_decode_diagnose
)
DIAGNOSE_JENKINS: TypedAgent[JenkinsDiagnoseRequest, JenkinsDiagnosis] = (
    _kit.mode_agent("maintainer", "diagnose", profile=_PROFILE, decode=_decode_jenkins)
)
FIX_CI: TypedAgent[FixRequest, Fixed] = _kit.mode_agent(
    "maintainer", "fix", profile=_PROFILE, decode=_decode_fix
)
RESOLVE_CONFLICTS: TypedAgent[ConflictRequest, Fixed] = _kit.mode_agent(
    "maintainer", "resolve-conflicts", profile=_PROFILE, decode=_decode_fix
)

__all__ = [
    "DIAGNOSE_CI",
    "DIAGNOSE_JENKINS",
    "FIX_CI",
    "RESOLVE_CONFLICTS",
    "ConflictRequest",
    "Diagnosis",
    "DiagnoseRequest",
    "Fixed",
    "FixRequest",
    "JenkinsDiagnoseRequest",
    "JenkinsDiagnosis",
]
