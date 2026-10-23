"""A fake coding agent: renders a real loop `Prompt`, then replays scripted tool calls and gates."""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from loop import Prompt

from .model import AgentConfig, Attempt, GateCheck, GateFailure, GateReport, ToolStep
from .runner import PhaseHandle

# Dollars per million tokens, for the fake usage breakdown.
_RATES = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}


class FakeAgent:
    """Stands in for a Copilot CLI agent; nothing is spawned."""

    def __init__(self, config: AgentConfig, system_prompt: str) -> None:
        self.config = config
        self.system_prompt = system_prompt
        self.session_id = str(uuid.uuid4())

    def call(self, ph: PhaseHandle, prompt: Prompt, output_type: str, gate: str,
             attempts: Sequence[Attempt]) -> dict[str, object]:
        """Plays attempts until one passes its gate; raises `GateFailure` when none does."""
        started_at = time.perf_counter()
        span = start_agent_span(
            ph, self.config, self.session_id, self.system_prompt, prompt.render())
        totals = dict.fromkeys(
            ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens",
             "cache_write_tokens"), 0)
        envelope: dict[str, object] = {}
        passed = False
        with trace.use_span(span, end_on_exit=False):
            for number, attempt in enumerate(attempts):
                ph.phase.attempt = number
                for step in attempt.steps:
                    self._tool_call(ph, step)
                self._add_usage(totals, random.Random(number + len(prompt.template)))
                envelope = attempt.envelope
                span.add_event("envelope", {
                    "loop.envelope.output_type": output_type,
                    "loop.envelope.payload": json.dumps(envelope),
                    "loop.envelope.valid": True, "loop.envelope.attempt": number})
                passed = record_gate(ph, gate, GateReport(attempt.checks), number)
                if passed:
                    break
                ph.run.sleep(0.4)
        self._finish(ph, span, totals, envelope, passed, time.perf_counter() - started_at)
        if not passed:
            raise GateFailure(f"{gate} failed after {len(attempts)} attempt(s)")
        return envelope

    def _tool_call(self, ph: PhaseHandle, step: ToolStep) -> None:
        with ph.tracer.start_as_current_span(
                f"execute_tool {step.tool}", kind=trace.SpanKind.INTERNAL,
                record_exception=False, set_status_on_exception=False) as span:
            ph.run.sleep(step.seconds)
            span.set_attributes({
                "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": step.tool,
                "gen_ai.tool.call.id": f"call_{uuid.uuid4().hex[:8]}",
                "gen_ai.tool.call.arguments": json.dumps(step.args),
                "gen_ai.tool.call.result": step.result,
                "gen_ai.agent.name": self.config.name, "loop.tool.ok": step.ok})
            span.set_status(Status(StatusCode.OK if step.ok else StatusCode.ERROR))

    @staticmethod
    def _add_usage(totals: dict[str, int], rng: random.Random) -> None:
        totals["input_tokens"] += rng.randint(3_000, 9_000)
        totals["output_tokens"] += rng.randint(800, 2_500)
        totals["reasoning_tokens"] += rng.randint(100, 500)
        totals["cache_read_tokens"] += rng.randint(20_000, 60_000)
        totals["cache_write_tokens"] += rng.randint(2_000, 8_000)

    def _finish(self, ph: PhaseHandle, span: Span, totals: dict[str, int],
                envelope: dict[str, object], passed: bool, seconds: float) -> None:
        cost = {name: totals[f"{name}_tokens"] * rate / 1_000_000 for name, rate in _RATES.items()}
        total_tokens = sum(totals[f"{name}_tokens"] for name in _RATES)
        context_tokens = min(total_tokens // 2, self.config.context_window)
        reason = "stop" if passed else "error"
        span.set_attributes({
            "gen_ai.response.model": self.config.model,
            "gen_ai.response.finish_reasons": [reason],
            "gen_ai.output.messages": json.dumps([{
                "role": "assistant", "finish_reason": reason,
                "parts": [{"type": "text", "content": json.dumps(envelope)}]}]),
            "gen_ai.usage.input_tokens": totals["input_tokens"],
            "gen_ai.usage.output_tokens": totals["output_tokens"],
            **{f"loop.usage.{name}": value for name, value in totals.items()},
            **{f"loop.usage.{name}_cost": value for name, value in cost.items()},
            "loop.usage.total_tokens": total_tokens,
            "loop.usage.total_cost": sum(cost.values()),
            "loop.agent.context_tokens": context_tokens})
        span.set_status(Status(StatusCode.OK))
        span.end()
        self._record_metrics(ph, totals, sum(cost.values()), reason, seconds)

    def _record_metrics(self, ph: PhaseHandle, totals: dict[str, int], cost: float,
                        reason: str, seconds: float) -> None:
        telemetry = ph.run.telemetry
        common = {"gen_ai.operation.name": "invoke_agent", "gen_ai.provider.name":
                  self.config.coding_agent, "gen_ai.request.model": self.config.model,
                  "gen_ai.agent.name": self.config.name}
        for token_type in ("input", "output"):
            telemetry.token_usage.record(
                totals[f"{token_type}_tokens"], {**common, "gen_ai.token.type": token_type})
        telemetry.operation_duration.record(
            seconds, {**common, **({} if reason == "stop" else {"error.type": "gate_failed"})})
        telemetry.cost.add(cost, common)


def record_gate(ph: PhaseHandle, gate: str, report: GateReport, attempt: int) -> bool:
    """Records a gate verdict as an instant span under the phase and returns it."""
    span = ph.tracer.start_span(
        f"gate {gate}", context=trace.set_span_in_context(ph.span), attributes={
            "loop.gate.name": gate, "loop.gate.attempt": attempt,
            "loop.gate.passed": report.passed,
            "loop.gate.checks": json.dumps([asdict(check) for check in report.checks]),
            "loop.gate.violations": json.dumps(report.violations)})
    span.set_status(Status(StatusCode.OK if report.passed else StatusCode.ERROR,
                           None if report.passed else "; ".join(report.violations)))
    span.end()
    ph.run.telemetry.gate_results.add(1, {"loop.gate.name": gate, "loop.gate.passed": report.passed})
    return report.passed


def start_agent_span(ph: PhaseHandle, config: AgentConfig, session_id: str, system_prompt: str,
                     user_prompt: str) -> Span:
    """Opens the `invoke_agent` span under the phase and logs its start marker."""
    attributes = {
        "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": config.name,
        "gen_ai.provider.name": config.coding_agent,
        "gen_ai.request.model": config.model, "gen_ai.conversation.id": session_id,
        "loop.agent.color": config.color,
        "loop.agent.coding_agent": config.coding_agent,
        "loop.agent.thinking": "medium",
        "loop.agent.purpose": ph.phase.params.description,
        "loop.agent.context_window": config.context_window}
    span = ph.tracer.start_span(
        f"invoke_agent {config.name}", kind=trace.SpanKind.CLIENT,
        attributes={**attributes,
                    "gen_ai.system_instructions": json.dumps(
                        [{"type": "text", "content": system_prompt}]),
                    "gen_ai.input.messages": json.dumps(
                        [{"role": "user", "parts": [{"type": "text", "content": user_prompt}]}])})
    ph.run.telemetry.started(span, "loop.agent.start", attributes, parent=ph.span)
    return span
