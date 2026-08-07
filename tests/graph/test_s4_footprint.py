"""Tests for S4 footprint module."""

import json
import tempfile
from pathlib import Path

import pytest

from harnessx.graph.footprint import (
    CoverageFootprint,
    FootprintStore,
    compute_footprint,
)
from harnessx.graph.observer import HookObservation, TaskTrace
from harnessx.graph.snapshot import to_graph
from harnessx.core.builder import HarnessBuilder
from harnessx.bundles import context


class TestCoverageFootprint:
    def test_create_empty(self):
        fp = CoverageFootprint(task_id="t1")
        assert fp.task_id == "t1"
        assert fp.touched_node_ids == set()
        assert fp.observed_edge_keys == set()

    def test_intersects_danger_nodes(self):
        fp = CoverageFootprint(
            task_id="t1",
            touched_node_ids={"proc:a", "hook:before_model"},
        )
        assert fp.intersects({"proc:a"}, set())
        assert not fp.intersects({"proc:b"}, set())

    def test_intersects_danger_edges(self):
        fp = CoverageFootprint(
            task_id="t1",
            observed_edge_keys={"proc:a→hook:before_model"},
        )
        assert fp.intersects(set(), {"proc:a→hook:before_model"})
        assert not fp.intersects(set(), {"proc:b→hook:after_model"})

    def test_roundtrip_dict(self):
        fp = CoverageFootprint(
            task_id="t1",
            variant_id="V0",
            touched_node_ids={"n1", "n2"},
            observed_edge_keys={"n1→n2"},
            step_count=5,
        )
        d = fp.to_dict()
        fp2 = CoverageFootprint.from_dict(d)
        assert fp2.task_id == "t1"
        assert fp2.touched_node_ids == {"n1", "n2"}
        assert fp2.observed_edge_keys == {"n1→n2"}

    def test_json_roundtrip(self):
        fp = CoverageFootprint(
            task_id="t1",
            touched_node_ids={"hook:before_model", "proc:system_prompt_processor"},
            observed_edge_keys={"proc:system_prompt_processor→hook:before_model"},
        )
        data = json.dumps(fp.to_dict())
        fp2 = CoverageFootprint.from_dict(json.loads(data))
        assert fp2.touched_node_ids == fp.touched_node_ids
        assert fp2.observed_edge_keys == fp.observed_edge_keys


class TestFootprintStore:
    def test_put_and_get(self):
        base = Path(tempfile.mkdtemp(prefix="fp_store_"))
        store = FootprintStore(base)
        fp = CoverageFootprint(
            task_id="t1",
            variant_id="V0",
            touched_node_ids={"n1"},
        )
        store.put(fp)
        loaded = store.get("V0", "t1")
        assert loaded is not None
        assert loaded.task_id == "t1"
        assert loaded.touched_node_ids == {"n1"}

    def test_get_missing(self):
        base = Path(tempfile.mkdtemp(prefix="fp_store_"))
        store = FootprintStore(base)
        assert store.get("V0", "nonexistent") is None

    def test_get_all(self):
        base = Path(tempfile.mkdtemp(prefix="fp_store_"))
        store = FootprintStore(base)
        store.put(CoverageFootprint(task_id="a", variant_id="V0", touched_node_ids={"x"}))
        store.put(CoverageFootprint(task_id="b", variant_id="V0", touched_node_ids={"y"}))
        all_fps = store.get_all("V0")
        assert len(all_fps) == 2

    def test_footprint_union(self):
        base = Path(tempfile.mkdtemp(prefix="fp_store_"))
        store = FootprintStore(base)
        store.put(CoverageFootprint(
            task_id="a", variant_id="V0",
            touched_node_ids={"n1", "n2"}, observed_edge_keys={"e1"},
        ))
        store.put(CoverageFootprint(
            task_id="b", variant_id="V0",
            touched_node_ids={"n2", "n3"}, observed_edge_keys={"e2"},
        ))
        nodes, edges = store.footprint_union("V0", ["a", "b"])
        assert nodes == {"n1", "n2", "n3"}
        assert edges == {"e1", "e2"}


class TestComputeFootprint:
    def test_maps_observations_to_graph(self):
        config = (HarnessBuilder() | context).build()
        snapshot = to_graph(config)

        trace = TaskTrace(task_id="t1", variant_id="V0")
        trace.record(HookObservation(
            step_id=1, hook_name="step_start",
            processor_label="SystemPromptProcessor",
        ))
        trace.record(HookObservation(
            step_id=2, hook_name="after_model",
            processor_label="model", tools_called=["Bash"],
        ))

        fp = compute_footprint(trace, snapshot)
        assert fp.task_id == "t1"
        # Should have at least the hook nodes touched
        assert len(fp.touched_node_ids) > 0
