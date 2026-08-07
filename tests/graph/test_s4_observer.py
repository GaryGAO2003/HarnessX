"""Tests for S4 observer module."""

import pytest

from harnessx.graph.observer import HookObservation, ObservationProcessor, TaskTrace


class TestHookObservation:
    def test_create(self):
        obs = HookObservation(
            step_id=1,
            hook_name="before_model",
            processor_label="SystemPrompt",
            slots_read=["memory"],
            tools_called=["Bash"],
        )
        assert obs.step_id == 1
        assert obs.hook_name == "before_model"
        assert "memory" in obs.slots_read
        assert "Bash" in obs.tools_called

    def test_defaults(self):
        obs = HookObservation(step_id=0, hook_name="step_start", processor_label="test")
        assert obs.slots_read == []
        assert obs.slots_written == []
        assert obs.tools_called == []


class TestTaskTrace:
    def test_record_accumulates(self):
        trace = TaskTrace(task_id="t1", variant_id="V0")
        trace.record(HookObservation(step_id=1, hook_name="step_start", processor_label="A"))
        trace.record(HookObservation(step_id=1, hook_name="before_model", processor_label="B"))
        assert len(trace.observations) == 2
        assert "A" in trace.touched_processors
        assert "B" in trace.touched_processors
        assert "step_start" in trace.touched_hooks

    def test_tools_tracked(self):
        trace = TaskTrace()
        trace.record(HookObservation(
            step_id=2, hook_name="after_model", processor_label="model",
            tools_called=["Bash", "Read"],
        ))
        assert "Bash" in trace.touched_tools
        assert "Read" in trace.touched_tools


class TestObservationProcessor:
    def test_creates_empty_trace(self):
        proc = ObservationProcessor(task_id="test", variant_id="V0")
        assert proc.trace.task_id == "test"
        assert len(proc.trace.observations) == 0

    def test_flush_resets(self):
        proc = ObservationProcessor(task_id="test")
        proc.trace.record(HookObservation(step_id=1, hook_name="task_start", processor_label="x"))
        trace = proc.flush()
        assert len(trace.observations) == 1
        # After flush, a new empty trace
        assert len(proc.trace.observations) == 0

    def test_hook_is_star(self):
        proc = ObservationProcessor()
        assert proc._hook == "*"

    def test_order_is_pre(self):
        proc = ObservationProcessor()
        assert proc._order == 0  # PRE — before other processors
