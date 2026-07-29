# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the labsmoke1 provenance gap — endpoint epoch in the lock.

The model can be pointed at a lab proxy via the ``DEEPSEEK_API_BASE`` env var.
Before M-23 that epoch was invisible: ``models.api_base`` stayed ``unresolved``
so an official-endpoint lock and a lab-endpoint lock were indistinguishable. The
fix records the endpoint URL (never the key) in ``EnvSpec.deepseek_api_base``,
which enters the lock sha and — because ``env`` is a lock-blocking section —
blocks a cross-epoch resume. An old lock written before the field existed parses
back to the ``official-default`` sentinel, so a legacy resume with no lab
endpoint set stays comparable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.experiment_lock import (  # noqa: E402
    OFFICIAL_DEFAULT_ENDPOINT,
    EnvSpec,
    ExperimentLock,
)
from experiments.variant_pool.resume import lock_blocking_diffs  # noqa: E402

_LAB_ENDPOINT = "https://lab.proxy.example/v1"


class _LockArgs:
    """Minimal CLI namespace covering the fields ``_build_experiment_lock`` reads."""

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
        self.__dict__.update(overrides)


def _make_lock(tmp_path: Path, **overrides) -> ExperimentLock:
    baseline = tmp_path / "V0" / "config.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline: true\n", encoding="utf-8")
    fake_base = SimpleNamespace(processors=[], tool_registry=None)
    return rvp._build_experiment_lock(
        args=_LockArgs(**overrides),
        run_tag="test-endpoint-epoch",
        baseline_config_path=baseline,
        original_base=fake_base,
    )


# ===========================================================================
# 1. the lock captures the endpoint epoch (set / unset), never the key
# ===========================================================================


def test_default_sentinel_when_env_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    lock = _make_lock(tmp_path)
    assert lock.env.deepseek_api_base == OFFICIAL_DEFAULT_ENDPOINT == "official-default"


def test_lab_endpoint_url_is_recorded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_BASE", _LAB_ENDPOINT)
    lock = _make_lock(tmp_path)
    assert lock.env.deepseek_api_base == _LAB_ENDPOINT
    assert _LAB_ENDPOINT in lock.to_json()


def test_api_key_is_never_written_to_the_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_BASE", _LAB_ENDPOINT)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-super-secret-never-persist")
    lock = _make_lock(tmp_path)
    rendered = lock.to_json()
    assert "sk-super-secret-never-persist" not in rendered
    assert "DEEPSEEK_API_KEY" not in rendered


def test_endpoint_epoch_enters_lock_identity(tmp_path, monkeypatch) -> None:
    """Two epochs are two run identities: the field is part of the sha."""
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    official = _make_lock(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_BASE", _LAB_ENDPOINT)
    lab = _make_lock(tmp_path)
    assert official.sha256() != lab.sha256()


# ===========================================================================
# 2. resume guardrail — cross-epoch is blocked, legacy is not
# ===========================================================================


def test_resume_blocks_official_to_lab(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    official = _make_lock(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_BASE", _LAB_ENDPOINT)
    lab = _make_lock(tmp_path)

    # resuming an official run under a lab endpoint (and vice versa) both block
    forward = lock_blocking_diffs(lab, official)
    reverse = lock_blocking_diffs(official, lab)
    assert any("env.deepseek_api_base" in entry for entry in forward)
    assert any("env.deepseek_api_base" in entry for entry in reverse)


def test_legacy_lock_without_field_plus_unset_env_resumes(tmp_path, monkeypatch) -> None:
    """Old lock (no field) + no lab endpoint set == comparable, no block."""
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    fresh = _make_lock(tmp_path)

    # Simulate a pre-M-23 lock on disk: its env section never had the field.
    old_dict = fresh.to_dict()
    del old_dict["env"]["deepseek_api_base"]
    import json

    legacy = ExperimentLock.from_json(json.dumps(old_dict))
    # the missing key parses back to the sentinel default (backward-compat rule)
    assert legacy.env.deepseek_api_base == OFFICIAL_DEFAULT_ENDPOINT

    new = _make_lock(tmp_path)  # env still unset -> also the sentinel
    blocking = lock_blocking_diffs(new, legacy)
    assert not any("deepseek_api_base" in entry for entry in blocking)


def test_legacy_lock_but_lab_endpoint_now_set_is_blocked(tmp_path, monkeypatch) -> None:
    """Old lock (sentinel) resumed under a lab endpoint is correctly blocked."""
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    fresh = _make_lock(tmp_path)
    old_dict = fresh.to_dict()
    del old_dict["env"]["deepseek_api_base"]
    import json

    legacy = ExperimentLock.from_json(json.dumps(old_dict))

    monkeypatch.setenv("DEEPSEEK_API_BASE", _LAB_ENDPOINT)
    lab_now = _make_lock(tmp_path)
    blocking = lock_blocking_diffs(lab_now, legacy)
    assert any("env.deepseek_api_base" in entry for entry in blocking)


def test_envspec_field_round_trips_and_defaults() -> None:
    """A hand-built EnvSpec defaults to the sentinel and survives JSON round-trip."""
    assert EnvSpec().deepseek_api_base == OFFICIAL_DEFAULT_ENDPOINT
    lock = ExperimentLock(
        experiment_id="epoch-test",
        created_at="2026-07-29T00:00:00Z",
        env=EnvSpec(deepseek_api_base=_LAB_ENDPOINT),
    )
    back = ExperimentLock.from_json(lock.to_json())
    assert back.env.deepseek_api_base == _LAB_ENDPOINT
    assert back.sha256() == lock.sha256()
