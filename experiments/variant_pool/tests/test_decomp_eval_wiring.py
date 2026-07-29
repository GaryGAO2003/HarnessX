# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the ``--decomp-eval`` wiring in ``run_variant_pool``.

Covers the CLI flag surface (defaults + acceptance), the byte-stability of the
default path (the pipeline classes are imported lazily, so the module namespace
never carries them), the frozen-pool loader (B0 single-variant + a monkeypatched
pool-from branch), and the two fail-fast guards (bad --decomp-source, and
--decomp-eval + --resume mutual exclusion). No provider, no rollout, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.pool import Variant, VariantPool  # noqa: E402


# ---------------------------------------------------------------------------
# CLI flag surface
# ---------------------------------------------------------------------------
def test_decomp_flag_defaults_full_table():
    args = rvp.build_arg_parser().parse_args([])
    assert args.decomp_eval is False
    assert args.decomp_source == "llm"
    assert args.decomp_routing == "single"
    assert args.decomp_pool_from is None
    assert args.decomp_profile_from is None
    assert args.decomp_max_subtasks == 5
    assert args.decomp_subtask_max_steps is None
    assert args.decomp_ledger_min_obs == 3
    assert args.decomp_verify_gate == "off"


def test_decomp_flags_accept_values():
    args = rvp.build_arg_parser().parse_args(
        [
            "--decomp-eval",
            "--decomp-source",
            "file:/tmp/oracle.json",
            "--decomp-routing",
            "ledger",
            "--decomp-pool-from",
            "runs/foo",
            "--decomp-profile-from",
            "runs/bar",
            "--decomp-max-subtasks",
            "4",
            "--decomp-subtask-max-steps",
            "12",
            "--decomp-ledger-min-obs",
            "2",
            "--decomp-verify-gate",
            "on",
        ]
    )
    assert args.decomp_eval is True
    assert args.decomp_source == "file:/tmp/oracle.json"
    assert args.decomp_routing == "ledger"
    assert args.decomp_pool_from == "runs/foo"
    assert args.decomp_profile_from == "runs/bar"
    assert args.decomp_max_subtasks == 4
    assert args.decomp_subtask_max_steps == 12
    assert args.decomp_ledger_min_obs == 2
    assert args.decomp_verify_gate == "on"


def test_decomp_routing_choices_are_enforced():
    with pytest.raises(SystemExit):
        rvp.build_arg_parser().parse_args(["--decomp-routing", "not-a-mode"])


def test_decomp_verify_gate_choices_are_enforced():
    with pytest.raises(SystemExit):
        rvp.build_arg_parser().parse_args(["--decomp-verify-gate", "maybe"])


# ---------------------------------------------------------------------------
# default-path byte stability: no eager decomp import
# ---------------------------------------------------------------------------
def test_default_path_never_touches_decomp_code():
    # The pipeline classes are imported INSIDE _run_decomp_eval, never at module
    # scope, so the default control flow (decomp_eval False) can never reach them.
    for symbol in ("PipelineExecutor", "SubtaskRouter", "TypeCreditLedger", "LlmDecomposer"):
        assert not hasattr(rvp, symbol)
    args = rvp.build_arg_parser().parse_args([])
    assert getattr(args, "decomp_eval", False) is False


def test_decomp_driver_functions_exist():
    assert callable(rvp._run_decomp_eval)
    assert callable(rvp._load_decomp_pool)


# ---------------------------------------------------------------------------
# frozen-pool loader
# ---------------------------------------------------------------------------
def test_load_decomp_pool_b0_single_variant(tmp_path):
    cfg = tmp_path / "V0" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("processors: []\n", encoding="utf-8")
    deps = {
        "baseline_config_path": cfg,
        "tasks": [SimpleNamespace(task_id="t1"), SimpleNamespace(task_id="t2")],
    }
    args = SimpleNamespace(decomp_pool_from=None, candidate_mode="paper")
    pool, variant_ids, choice, source = rvp._load_decomp_pool(args, deps, tmp_path)
    assert variant_ids == ["V0"]
    assert set(pool.variants) == {"V0"}
    assert pool.variants["V0"].config_path == Path(cfg)
    assert choice("t1") == "V0"
    assert choice("anything") == "V0"
    assert source == {"path": None, "round": None, "mode": "h0_single"}


def test_load_decomp_pool_from_run_dir(tmp_path, monkeypatch):
    pool = VariantPool(K=2)
    pool.add_root(tmp_path / "c0.yaml", tmp_path / "j0", tasks=["t1", "t2"])
    pool.variants["V1"] = Variant(
        variant_id="V1",
        config_path=tmp_path / "c1.yaml",
        journal_path=tmp_path / "j1",
        created_round=1,
        parent_id="V0",
        routed_tasks={"t2"},
    )
    pool.next_id = 2

    fake_state = SimpleNamespace(next_id=2, next_round=2, last_settled_round=1, variants=(1, 2))
    monkeypatch.setattr(rvp, "load_resume_state", lambda *a, **k: fake_state)
    monkeypatch.setattr(rvp, "rebuild_pool", lambda state, K: pool)
    monkeypatch.setattr(rvp, "replay_ledger", lambda state, ledger: ledger)

    deps = {
        "tasks": [SimpleNamespace(task_id="t1"), SimpleNamespace(task_id="t2")],
        "baseline_config_path": tmp_path / "c0.yaml",
    }
    args = SimpleNamespace(decomp_pool_from=str(tmp_path), candidate_mode="paper")
    got_pool, variant_ids, choice, source = rvp._load_decomp_pool(args, deps, tmp_path)
    assert got_pool is pool
    assert variant_ids == ["V0", "V1"]
    assert choice("t1") in {"V0", "V1"}  # deployment-time route resolves to a pool variant
    assert choice("t2") in {"V0", "V1"}
    assert source == {"path": str(tmp_path), "round": 1}


# ---------------------------------------------------------------------------
# fail-fast guards
# ---------------------------------------------------------------------------
def test_run_decomp_eval_rejects_bad_source(tmp_path):
    args = SimpleNamespace(decomp_source="bogus")
    with pytest.raises(SystemExit) as excinfo:
        rvp._run_decomp_eval(args, tmp_path, {})
    assert "decomp-source" in str(excinfo.value)


def test_run_decomp_eval_end_to_end_offline(tmp_path, monkeypatch):
    """Drive the full evaluation loop with every heavy seam stubbed.

    No provider, no HarnessConfig, no rollout: ``_make_provider``,
    ``_make_journal``, ``_prepare_round_config`` and ``_rollout_once`` are
    monkeypatched, decomposition comes from a ``file:`` oracle, and the
    evaluator is a stub. Exercises pool load -> per-task pass-k pipeline ->
    pass@k -> manifest/summary/jsonl/plans落盘.
    """
    import json as _json

    oracle = tmp_path / "oracle.json"
    oracle.write_text(
        _json.dumps(
            {
                "t1": [
                    {"id": "s1", "type": "search", "instruction": "find X", "dep": []},
                    {"id": "s2", "type": "compute", "instruction": "use X", "dep": ["s1"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    class _FakeProvider:
        async def complete(self, messages, tools, stream_callback=None):
            return SimpleNamespace(content="FINAL ANSWER: 42")

    async def _fake_rollout(task, attempt, *, label, model_config, round_config, pipeline_eval, max_cost):
        return {"final_output": f"out::{task.task_id}", "steps": 2, "cost_usd": 0.001}

    class _FakeEval:
        async def evaluate_answer(self, final_output, ground_truth):
            return SimpleNamespace(passed=("42" in (final_output or "")), score=1.0, reason="", reward=1.0)

    monkeypatch.setattr(rvp, "_make_provider", lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(rvp, "_make_journal", lambda d: None)
    monkeypatch.setattr(rvp, "_prepare_round_config", lambda cfg, journal: object())
    monkeypatch.setattr(rvp, "_rollout_once", _fake_rollout)

    cfg = tmp_path / "V0" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("processors: []\n", encoding="utf-8")
    deps = {
        "tasks": [SimpleNamespace(task_id="t1", question="the question", final_answer="42", level=1)],
        "pipeline_eval": _FakeEval(),
        "model_config": object(),
        "baseline_config_path": cfg,
    }
    args = SimpleNamespace(
        decomp_source=f"file:{oracle}",
        decomp_routing="single",
        decomp_pool_from=None,
        decomp_profile_from=None,
        decomp_max_subtasks=5,
        decomp_subtask_max_steps=None,
        decomp_ledger_min_obs=3,
        decomp_verify_gate="off",
        max_steps=10,
        max_cost=1.0,
        pass_k=2,
        model="m",
        meta_model="mm",
        provider_id="p",
        candidate_mode="paper",
    )
    rvp._run_decomp_eval(args, tmp_path, deps)

    manifest = _json.loads((tmp_path / "decomp_manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "decomp_eval"
    assert manifest["decomp_source"].startswith("file:")
    assert manifest["pool_source"]["mode"] == "h0_single"
    assert manifest["decomp_subtask_max_steps"] == 10  # inherited from --max-steps

    summary = _json.loads((tmp_path / "decomp_summary.json").read_text(encoding="utf-8"))
    assert summary["num_tasks"] == 1
    assert summary["fallback_rate"] == 0.0
    assert summary["pass_at_k"] == pytest.approx(1.0)  # synthesis always yields "42"
    assert "V0::search" in summary["credit_matrix"]
    assert "V0::compute" in summary["credit_matrix"]

    lines = (tmp_path / "decomp_tasks.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # pass_k = 2 attempts
    rec0 = _json.loads(lines[0])
    assert rec0["passed"] is True
    assert rec0["fallback"] is False
    assert [s["id"] for s in rec0["subtasks"]] == ["s1", "s2"]

    plans = _json.loads((tmp_path / "decomp_plans.json").read_text(encoding="utf-8"))
    assert "t1" in plans


def test_main_rejects_decomp_eval_with_resume(monkeypatch):
    argv = [
        "run_variant_pool",
        "--model",
        "m",
        "--meta-model",
        "mm",
        "--provider-id",
        "p",
        "--decomp-eval",
        "--resume",
        "runs/whatever",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        rvp.main()
    assert "resume" in str(excinfo.value).lower()
