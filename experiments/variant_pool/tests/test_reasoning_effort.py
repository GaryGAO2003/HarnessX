# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the ``--reasoning-effort`` / ``--meta-reasoning-effort`` flags.

The lab LiteLLM/vLLM endpoint accepts a ``reasoning_effort`` request field
(none/low/medium/high). Two CLI flags expose it:

* ``--reasoning-effort``       → the task (inner) agent model.
* ``--meta-reasoning-effort``  → the meta agent (Digester/Planner/Evolver/Critic),
  falling back to ``--reasoning-effort`` when unset.

Three invariants are pinned here:

1. **Byte-equivalence.** With neither flag set, no ``reasoning_effort`` key is put
   on the provider, so the request body is byte-identical to a pre-flag run. (The
   *lock* is allowed to record an explicit ``null`` — that is provenance, not the
   request body.)
2. **Wiring.** ``high`` reaches the task provider and, by fallback, the meta
   provider; two different values do not cross.
3. **Resume back-compat.** A legacy lock written before these fields existed has
   neither ``models`` key; it parses back to ``None`` and — with the flags still
   unset — does not block a resume.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.experiment_lock import (  # noqa: E402
    ExperimentLock,
    ModelSpec,
)
from experiments.variant_pool.resume import lock_blocking_diffs  # noqa: E402


class _LockArgs:
    """Minimal CLI namespace covering the fields ``_build_experiment_lock`` reads.

    Mirrors ``test_lock_endpoint_epoch._LockArgs`` (the proven minimal set) plus
    the two reasoning-effort flags, defaulted to ``None`` so overrides opt in.
    """

    def __init__(self, **overrides) -> None:
        self.pool_k = 2
        self.pass_k = 2
        self.num_rounds = 1
        self.max_steps = 20
        self.concurrency = 2
        self.evolve_steps = 200
        self.patience = 1
        self.seed = 0
        self.estimator = "laplace"
        self.candidate_mode = "legacy_single"
        self.candidates_per_round = 4
        self.model = "task-model"
        self.meta_model = "meta-model"
        self.provider_id = "provider"
        self.api_base = None
        self.data_path = None
        self.planned_seeds = (0, 1, 2)
        self.reasoning_effort = None
        self.meta_reasoning_effort = None
        self.__dict__.update(overrides)


def _make_lock(tmp_path: Path, **overrides) -> ExperimentLock:
    baseline = tmp_path / "V0" / "config.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline: true\n", encoding="utf-8")
    fake_base = SimpleNamespace(processors=[], tool_registry=None)
    return rvp._build_experiment_lock(
        args=_LockArgs(**overrides),
        run_tag="test-reasoning-effort",
        baseline_config_path=baseline,
        original_base=fake_base,
    )


# ===========================================================================
# 1. effort resolution (fallback + non-crossing) at the args level
# ===========================================================================


@pytest.mark.parametrize(
    "task, meta, exp_task, exp_meta",
    [
        (None, None, None, None),        # neither set => omit for both
        ("high", None, "high", "high"),  # meta falls back to task
        ("high", "low", "high", "low"),  # both set, independent, no crossing
        (None, "low", None, "low"),      # meta set, task stays unset
        ("none", None, "none", "none"),  # literal "none" is a real value + falls back
        ("high", "none", "high", "none"),  # meta "none" overrides the fallback
    ],
)
def test_effort_resolution(task, meta, exp_task, exp_meta) -> None:
    args = SimpleNamespace(reasoning_effort=task, meta_reasoning_effort=meta)
    assert rvp._task_reasoning_effort(args) == exp_task
    assert rvp._meta_reasoning_effort(args) == exp_meta


def test_resolution_tolerates_args_without_the_attributes() -> None:
    """A legacy ``args`` object with neither attribute resolves to None (byte-safe)."""
    args = SimpleNamespace()  # no reasoning_effort / meta_reasoning_effort at all
    assert rvp._task_reasoning_effort(args) is None
    assert rvp._meta_reasoning_effort(args) is None


# ===========================================================================
# 2. provider wiring + byte-equivalence (the request body)
# ===========================================================================


def test_no_flags_leaves_no_reasoning_effort_on_the_request() -> None:
    """Unset => no ``reasoning_effort`` key on the provider => byte-identical request."""
    implicit = rvp._make_provider("task-model", "provider")
    explicit_none = rvp._make_provider("task-model", "provider", reasoning_effort=None)
    assert "reasoning_effort" not in implicit.kwargs
    assert "reasoning_effort" not in explicit_none.kwargs
    # passing the default explicitly changes nothing about the forwarded kwargs
    assert implicit.kwargs == explicit_none.kwargs


def test_no_flags_byte_identical_on_the_lab_endpoint_branch() -> None:
    """Same guarantee on the ``api_base`` (lab vLLM) branch used by the task model."""
    implicit = rvp._make_provider("task-model", "provider", api_base="https://lab/v1", api_key="EMPTY")
    explicit_none = rvp._make_provider(
        "task-model", "provider", api_base="https://lab/v1", api_key="EMPTY", reasoning_effort=None
    )
    assert "reasoning_effort" not in implicit.kwargs
    assert implicit.kwargs == explicit_none.kwargs


def test_effort_is_forwarded_when_set_on_both_branches() -> None:
    header_branch = rvp._make_provider("task-model", "provider", reasoning_effort="high")
    api_base_branch = rvp._make_provider(
        "task-model", "provider", api_base="https://lab/v1", api_key="EMPTY", reasoning_effort="high"
    )
    assert header_branch.kwargs.get("reasoning_effort") == "high"
    assert api_base_branch.kwargs.get("reasoning_effort") == "high"


def test_literal_none_is_still_sent() -> None:
    """``--reasoning-effort none`` is a real endpoint value (disable thinking), not omit."""
    provider = rvp._make_provider("task-model", "provider", reasoning_effort="none")
    assert provider.kwargs.get("reasoning_effort") == "none"


def test_task_and_meta_providers_carry_independent_effort() -> None:
    """Reproduces setup()'s two call sites: task gets task effort, meta gets meta effort."""
    args = SimpleNamespace(reasoning_effort="high", meta_reasoning_effort="low")
    task_provider = rvp._make_provider(
        "task-model", "provider", reasoning_effort=rvp._task_reasoning_effort(args)
    )
    meta_provider = rvp._make_provider(
        "meta-model",
        "provider",
        extended_thinking=True,
        thinking_budget_tokens=32_000,
        max_tokens=40_000,
        reasoning_effort=rvp._meta_reasoning_effort(args),
    )
    assert task_provider.kwargs.get("reasoning_effort") == "high"
    assert meta_provider.kwargs.get("reasoning_effort") == "low"
    # non-crossing: the two efforts stay distinct
    assert task_provider.kwargs["reasoning_effort"] != meta_provider.kwargs["reasoning_effort"]


def test_meta_provider_inherits_task_effort_when_meta_flag_unset() -> None:
    args = SimpleNamespace(reasoning_effort="high", meta_reasoning_effort=None)
    meta_provider = rvp._make_provider(
        "meta-model", "provider", reasoning_effort=rvp._meta_reasoning_effort(args)
    )
    assert meta_provider.kwargs.get("reasoning_effort") == "high"


# ===========================================================================
# 3. lock provenance — effective values captured in the models section
# ===========================================================================


def test_lock_records_independent_task_and_meta_efforts(tmp_path) -> None:
    lock = _make_lock(tmp_path, reasoning_effort="high", meta_reasoning_effort="low")
    assert lock.models.reasoning_effort == "high"
    assert lock.models.meta_reasoning_effort == "low"


def test_lock_records_meta_fallback_to_task(tmp_path) -> None:
    lock = _make_lock(tmp_path, reasoning_effort="high")  # meta flag unset
    assert lock.models.reasoning_effort == "high"
    assert lock.models.meta_reasoning_effort == "high"


def test_lock_without_flags_is_null_not_empty(tmp_path) -> None:
    """Chosen provenance form (SPEC req 4): unset => explicit ``null`` keys, never ""."""
    lock = _make_lock(tmp_path)
    assert lock.models.reasoning_effort is None
    assert lock.models.meta_reasoning_effort is None
    rendered = lock.to_json()
    assert '"reasoning_effort": null' in rendered
    assert '"meta_reasoning_effort": null' in rendered


# ===========================================================================
# 4. resume guardrail — legacy lock (no keys) is treated as unset
# ===========================================================================


def test_legacy_lock_without_effort_keys_resumes(tmp_path, monkeypatch) -> None:
    """Old lock (no models.reasoning_effort keys) + flags unset == comparable, no block."""
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    fresh = _make_lock(tmp_path)

    # Simulate a pre-flag lock on disk: its models section never had the fields.
    old_dict = fresh.to_dict()
    del old_dict["models"]["reasoning_effort"]
    del old_dict["models"]["meta_reasoning_effort"]

    legacy = ExperimentLock.from_json(json.dumps(old_dict))
    # the missing keys parse back to the None default (backward-compat rule)
    assert legacy.models.reasoning_effort is None
    assert legacy.models.meta_reasoning_effort is None

    new = _make_lock(tmp_path)  # resume with the flags still unset
    blocking = lock_blocking_diffs(new, legacy)
    assert not any("reasoning_effort" in entry for entry in blocking)
    assert blocking == []  # a same-config legacy resume is fully unblocked


def test_legacy_lock_but_effort_now_set_is_blocked(tmp_path, monkeypatch) -> None:
    """Setting an effort when resuming a pre-flag run correctly forks the family."""
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    fresh = _make_lock(tmp_path)
    old_dict = fresh.to_dict()
    del old_dict["models"]["reasoning_effort"]
    del old_dict["models"]["meta_reasoning_effort"]
    legacy = ExperimentLock.from_json(json.dumps(old_dict))

    now_high = _make_lock(tmp_path, reasoning_effort="high")
    blocking = lock_blocking_diffs(now_high, legacy)
    assert any("models.reasoning_effort" in entry for entry in blocking)


# ===========================================================================
# 5. ModelSpec field defaults + JSON round-trip
# ===========================================================================


def test_modelspec_effort_defaults_and_round_trip() -> None:
    assert ModelSpec().reasoning_effort is None
    assert ModelSpec().meta_reasoning_effort is None
    lock = ExperimentLock(
        experiment_id="effort-roundtrip",
        created_at="2026-07-30T00:00:00Z",
        models=ModelSpec(
            task_agent_model="t",
            meta_agent_model="m",
            reasoning_effort="high",
            meta_reasoning_effort="low",
        ),
    )
    back = ExperimentLock.from_json(lock.to_json())
    assert back.models.reasoning_effort == "high"
    assert back.models.meta_reasoning_effort == "low"
    assert back.sha256() == lock.sha256()
