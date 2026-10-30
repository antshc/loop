from __future__ import annotations

import pytest

from software_factory.shared.github.issues.issue_classifier import (
    Classification,
    IssueClassifier,
    IssueKind,
    LabelTypeDisagreement,
)
from tests.shared.builders import build_issue


@pytest.mark.parametrize(
    ("labels", "issue_type", "expected"),
    [
        (("spec",), None, Classification(IssueKind.SPEC)),
        (("SPEC", "repo:base:main"), None, Classification(IssueKind.SPEC)),
        ((), None, Classification(IssueKind.ISSUE)),
        (("bug",), None, Classification(IssueKind.ISSUE)),
        (("spec",), "Spec", Classification(IssueKind.SPEC)),
        ((), "Feature", Classification(IssueKind.ISSUE)),
        (("spec",), "Task", Classification(IssueKind.SPEC, LabelTypeDisagreement(True, "Task"))),
        ((), "Spec", Classification(IssueKind.ISSUE, LabelTypeDisagreement(False, "Spec"))),
    ],
    ids=[
        "label alone",
        "label is case-insensitive",
        "no label, no type",
        "other label only",
        "label and type agree",
        "neither says spec",
        "label wins over another type",
        "type says spec but the label is missing",
    ],
)
def test_the_spec_label_decides_and_a_disagreeing_native_type_is_reported(
    labels: tuple[str, ...], issue_type: str | None, expected: Classification
) -> None:
    assert IssueClassifier().classify(build_issue(labels=labels, issue_type=issue_type)) == expected


def test_a_disagreement_names_both_sides() -> None:
    message = str(LabelTypeDisagreement(True, "Task"))

    assert "`spec` label" in message and "'Task'" in message
