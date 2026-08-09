"""S4 — ObservationProcessor that records graph activity at runtime.

Registers as a ``MultiHookProcessor`` on ``"*"`` (every hook) and emits
structured observation records without changing any ``run_loop`` behaviour.

Each observation maps to graph coordinates (which processor, which hook,
which step) so that S4 can produce per-task coverage footprints and S6
can perform three-way reconciliation (declared vs observed vs absent).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harnessx.core.processor import MultiHookProcessor


@dataclass
class HookObservation:
    """One observation of graph activity at a single hook point."""

    step_id: int
    hook_name: str
    processor_label: str  # class name or _target_
    processor_target: str = ""  # qualified _target_ path

    # slot activity observed during this hook invocation
    slots_read: list[str] = field(default_factory=list)
    slots_written: list[str] = field(default_factory=list)

    # tool invocations observed
    tools_called: list[str] = field(default_factory=list)

    # Δ11 backlink anchor: the journal session-line uuid current at capture
    # time ("" when no journal is bound — e.g. NullTracer)
    journal_uuid: str = ""


@dataclass
class TaskTrace:
    """All observations from one task execution."""

    task_id: str = ""
    variant_id: str = ""
    run_id: str = ""
    observations: list[HookObservation] = field(default_factory=list)

    # derived
    touched_processors: set[str] = field(default_factory=set)
    touched_hooks: set[str] = field(default_factory=set)
    touched_tools: set[str] = field(default_factory=set)

    def record(self, obs: HookObservation) -> None:
        self.observations.append(obs)
        if obs.processor_label:
            self.touched_processors.add(obs.processor_label)
        if obs.hook_name:
            self.touched_hooks.add(obs.hook_name)
        for tool in obs.tools_called:
            self.touched_tools.add(tool)


class ObservationProcessor(MultiHookProcessor):
    """A ``MultiHookProcessor`` that records graph activity at every hook.

    Register under ``"*"`` to observe all 10 hooks without modifying
    ``run_loop``.

    Usage::

        obs = ObservationProcessor(task_id="gaia_001")
        config = HarnessBuilder().add(obs) | context
        result = await harness.run(task)
        trace = obs.flush()  # → TaskTrace

        from harnessx.graph.footprint import compute_footprint
        fp = compute_footprint(trace, snapshot)
    """

    _hook = "*"
    _order = 0  # PRE — observe before other processors

    def __init__(self, task_id: str = "", variant_id: str = ""):
        super().__init__()
        self._trace = TaskTrace(task_id=task_id, variant_id=variant_id)
        self._current_step = 0

    @property
    def trace(self) -> TaskTrace:
        return self._trace

    def flush(self) -> TaskTrace:
        """Return the collected trace and reset for the next task."""
        t = self._trace
        self._trace = TaskTrace(task_id="", variant_id="")
        self._current_step = 0
        return t

    # ── hook handlers ──────────────────────────────────────────────────

    async def on_task_start(self, event):
        self._trace.run_id = getattr(event, "run_id", "")
        yield event

    async def on_step_start(self, event):
        self._current_step = getattr(event, "step_id", self._current_step + 1)
        yield event

    async def on_before_model(self, event):
        self._record("before_model")
        yield event

    def _journal_uuid(self) -> str:
        """Current journal line uuid from the bound tracer (Δ11 anchor)."""
        rt = getattr(self, "_harness_runtime", None)
        return getattr(getattr(rt, "tracer", None), "last_uuid", None) or ""

    async def on_after_model(self, event):
        obs = HookObservation(
            step_id=self._current_step,
            hook_name="after_model",
            processor_label="model",
            processor_target="",
            journal_uuid=self._journal_uuid(),
        )
        # Record tool calls the model requested
        tool_calls = getattr(event, "tool_calls", None) or []
        for tc in tool_calls:
            name = getattr(tc, "name", "") or tc.get("name", "") if isinstance(tc, dict) else ""
            if name:
                obs.tools_called.append(name)
        self._trace.record(obs)
        yield event

    async def on_before_tool(self, event):
        tool_name = getattr(event, "tool_name", "")
        obs = HookObservation(
            step_id=self._current_step,
            hook_name="before_tool",
            processor_label=f"tool:{tool_name}" if tool_name else "before_tool",
            journal_uuid=self._journal_uuid(),
        )
        if tool_name:
            obs.tools_called.append(tool_name)
        self._trace.record(obs)
        yield event

    async def on_after_tool(self, event):
        self._record("after_tool", label="after_tool")
        yield event

    async def on_step_end(self, event):
        self._record("step_end")
        yield event

    async def on_task_end(self, event):
        self._record("task_end")
        yield event

    def _record(self, hook_name: str, label: str = "") -> None:
        obs = HookObservation(
            step_id=self._current_step,
            hook_name=hook_name,
            processor_label=label or hook_name,
            journal_uuid=self._journal_uuid(),
        )
        self._trace.record(obs)
