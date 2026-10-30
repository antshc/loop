"""A fake e-commerce delivery that demonstrates optional phase reporting."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from loop import Kind, Role, WorkflowBuilder, configure_logging, otlp_observer


def main(argv: Sequence[str] | None = None) -> int:
    """Runs a fake delivery without agents, Git, or GitHub calls."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visualize", action="store_true")
    parser.add_argument("--fail-verification", action="store_true")
    parser.add_argument("--otel-endpoint", default=None)
    args = parser.parse_args(argv)
    configure_logging()

    builder = (
        WorkflowBuilder("eShop development")
        .phase("prepare", "Prepare repository", Kind.CODE, Role.CODE)
        .phase("implement", "Implement ticket", Kind.AGENT, Role.DEVELOPER)
        .phase("publish", "Publish draft PR", Kind.CODE, Role.CODE)
        .phase("verify", "Verify application", Kind.AGENT, "verifier")
        .phase("chore", "Refine changes", Kind.AGENT, "chore")
        .phase("review", "Review pull request", Kind.AGENT, Role.REVIEWER)
        .phase("finalize", "Check readiness", Kind.CODE, Role.CODE)
        .console()
    )
    if args.visualize:
        builder.visualize()
    if args.otel_endpoint:
        observer = otlp_observer(endpoint=args.otel_endpoint)
        builder.otel(observer)

    with builder.build().run(spec=42) as run:
        with run.phase("prepare"):
            basket: list[str] = []

        for ticket, product in ((43, "coffee"), (44, "tea")):
            for attempt in range(1, 3):
                with run.phase("implement", ticket=ticket, attempt=attempt) as phase:
                    # One deliberate failure demonstrates a recovered retry.
                    changed = not (ticket == 43 and attempt == 1)
                    if phase.gate("product added", changed, "No changes produced"):
                        basket.append(product)
                if phase.status == "completed":
                    break
            else:
                run.fail(f"Ticket #{ticket} exhausted its retries")
                return 1

        with run.phase("publish") as phase:
            phase.set(pull_request="fake://eshop/pull/57")

        with run.phase("verify") as phase:
            verified = basket == ["coffee", "tea"] and not args.fail_verification
            phase.set(verdict="pass" if verified else "fail")
            phase.gate("basket contains the expected products", verified, "Basket verification failed")

        run.skip("chore", "No cleanup configured")
        if not verified:
            run.skip("review", "Verification failed")
            run.skip("finalize", "PR remains a draft")
            run.set(ready=False, verdict="fail")
            run.fail("Application verification failed; PR remains a draft")
            return 1

        # A real workflow closes its implementation worktree before review.
        with run.phase("review") as phase:
            phase.set(blocking_findings=0)
        with run.phase("finalize") as phase:
            phase.set(ready=True)
        run.set(ready=True, verdict="pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
