from __future__ import annotations

import logging

from ..phases import Phase

logger = logging.getLogger("loop.workflow")


def label(phase: Phase) -> str:
    result = phase.label
    for name, prefix in (("spec", "spec #"), ("ticket", "ticket #"), ("attempt", "attempt ")):
        if name in phase.attributes:
            result += f" · {prefix}{phase.attributes[name]}"
    return result


class ConsoleReporter:
    """Writes only phase transitions and short failure summaries."""

    def emit(self, point: str, phase: Phase) -> None:
        if not phase.visible:
            return
        if point == "started":
            logger.info("%s started", label(phase))
            return
        level = logging.WARNING if phase.status in ("failed", "cancelled") else logging.INFO
        suffix = ""
        for key in ("verdict", "ready", "blocking_findings", "planned", "pull_request"):
            if key in phase.attributes:
                value = " ".join(str(phase.attributes[key]).split())[:200]
                suffix += f" · {key}={value}"
        if phase.reason:
            suffix += " · " + " ".join(phase.reason.split())[:200]
        logger.log(level, "%s %s · %.1fs%s", label(phase), phase.status, phase.seconds, suffix)
