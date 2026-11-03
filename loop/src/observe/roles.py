from __future__ import annotations


class Role:
    """Shared resource names for observation: a backend such as Aspire colors each role apart, so Workflows reuse them."""

    # The person whose request starts a run.
    ENGINEER = "engineer"
    # Deterministic steps: Git, GitHub, checks, validation.
    CODE = "code"
    PLANNER = "planner"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    ALL = (ENGINEER, CODE, PLANNER, DEVELOPER, REVIEWER)
