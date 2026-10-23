"""A fake coding agent: renders a real loop `Prompt`, then replays scripted tool calls and gates."""

from __future__ import annotations

import json
import random
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from loop import Prompt

from .model import AgentConfig, EventRecord, GateCheck, GateReport, now_iso
from .runner import PhaseHandle

# Dollars per million tokens, for the fake usage breakdown.
_RATES = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}


class GateFailure(Exception):
    """Every scripted attempt failed its gate."""


@dataclass(frozen=True)
class ToolStep:
    """One scripted tool call."""

    tool: str
    args: dict[str, str]
    result: str
    ok: bool = True
    seconds: float = 0.6


@dataclass(frozen=True)
class Attempt:
    """One send to the agent: the tool calls it makes, its answer, and the gate evidence."""

    steps: tuple[ToolStep, ...]
    envelope: dict[str, object]
    checks: tuple[GateCheck, ...]


class FakeAgent:
    """Stands in for a Copilot CLI agent; nothing is spawned."""

    def __init__(self, config: AgentConfig, system_prompt: str) -> None:
        self.config = config
        self.system_prompt = system_prompt
        self.session_id = str(uuid.uuid4())

    def call(self, ph: PhaseHandle, prompt: Prompt, output_type: str, gate: str,
             attempts: Sequence[Attempt]) -> dict[str, object]:
        """Plays attempts until one passes its gate; raises `GateFailure` when none does."""
        run, tracer = ph.run, ph.run.tracer
        self._save_prompts(ph, prompt.render())
        start_id = self._emit(ph, "agent_start", self.config.name, {
            "model": self.config.model, "thinking": "medium", "color": self.config.color,
            "session_id": self.session_id, "coding_agent": self.config.coding_agent,
            "purpose": ph.phase.params.description, "tools": None, "harness_engineering": []})
        totals = dict.fromkeys(
            ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens",
             "cache_write_tokens"), 0)
        envelope: dict[str, object] = {}
        passed = False
        for number, attempt in enumerate(attempts):
            ph.phase.attempt = number
            tracer.phase_upsert(ph.phase)
            for step in attempt.steps:
                self._tool_call(ph, start_id, step)
            self._add_usage(totals, random.Random(number + len(prompt.template)))
            envelope = attempt.envelope
            tracer.envelope_row(ph.phase, self.config.name, output_type,
                                json.dumps(envelope), True, number)
            report = GateReport(attempt.checks)
            tracer.gate_row(ph.phase, gate, report, number)
            passed = report.passed
            self._emit(ph, "gate_pass" if passed else "gate_fail", gate,
                       {"attempt": number, "checks": [asdict(c) for c in report.checks],
                        "violations": report.violations})
            if passed:
                break
            run.sleep(0.4)
        self._finish(ph, totals)
        if not passed:
            raise GateFailure(f"{gate} failed after {len(attempts)} attempt(s)")
        return envelope

    def _save_prompts(self, ph: PhaseHandle, user_prompt: str) -> None:
        directory = ph.run.session_dir / self.config.name / "prompts"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "system.md").write_text(self.system_prompt)
        (directory / "user.md").write_text(user_prompt)

    def _tool_call(self, ph: PhaseHandle, parent_id: str, step: ToolStep) -> None:
        started = now_iso()
        ph.run.sleep(step.seconds)
        summary = next(iter(step.args.values()))
        ph.run.tracer.event(EventRecord(
            adw_id=ph.run.adw_id, phase_id=ph.phase.phase_id, parent_id=parent_id,
            type="tool_call", name=f"{step.tool}: {summary}",
            payload={"tool": step.tool, "tool_call_id": f"call_{uuid.uuid4().hex[:8]}",
                     "args": step.args, "result_snippet": step.result, "ok": step.ok,
                     "duration_ms": int(step.seconds * 1000), "agent": self.config.name},
            started_at=started, ended_at=now_iso()))

    @staticmethod
    def _add_usage(totals: dict[str, int], rng: random.Random) -> None:
        totals["input_tokens"] += rng.randint(3_000, 9_000)
        totals["output_tokens"] += rng.randint(800, 2_500)
        totals["reasoning_tokens"] += rng.randint(100, 500)
        totals["cache_read_tokens"] += rng.randint(20_000, 60_000)
        totals["cache_write_tokens"] += rng.randint(2_000, 8_000)

    def _finish(self, ph: PhaseHandle, totals: dict[str, int]) -> None:
        cost = {name: totals[f"{name}_tokens"] * rate / 1_000_000 for name, rate in _RATES.items()}
        total_tokens = sum(totals[f"{name}_tokens"] for name in _RATES)
        total_cost = sum(cost.values())
        context_tokens = min(total_tokens // 2, self.config.context_window)
        usage = {**totals, "total_tokens": total_tokens,
                 **{f"{name}_cost": value for name, value in cost.items()},
                 "total_cost": total_cost}
        ph.run.tracer.event(EventRecord(
            adw_id=ph.run.adw_id, phase_id=ph.phase.phase_id, type="agent_end",
            name=self.config.name, tokens=total_tokens,
            payload={"cost": total_cost, "usage": usage, "context_tokens": context_tokens,
                     "context_window": self.config.context_window}))
        ph.run.add_usage(total_tokens, total_cost)
        ph.run.tracer.agent_session_row(ph.run.adw_id, self.config, self.session_id,
                                        context_tokens)

    @staticmethod
    def _emit(ph: PhaseHandle, event_type: str, name: str, payload: dict[str, object]) -> str:
        return ph.run.tracer.event(EventRecord(
            adw_id=ph.run.adw_id, phase_id=ph.phase.phase_id, type=event_type, name=name,
            payload=payload))
