# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.experiment_lock`` (W26).

The lock is the reason an M0 number can be attributed to anything (SPEC §6.3),
so the tests are about the two promises it makes: it round-trips without losing
a type, and it can tell an *ablation arm* (hyperparameters moved) from a *new
experiment family* (H0, models or data moved).

Nothing here reads the clock, git or the network: ``created_at`` and ``git_sha``
are injected, exactly as the module requires of its callers.
"""

from __future__ import annotations

import json

import pytest

from variant_pool.experiment_lock import (
    FAMILY_SECTIONS,
    LOCK_FILENAME,
    PAPER_LEVEL_DISTRIBUTION,
    SPEC_VERSION,
    DatasetSpec,
    EnvSpec,
    ExperimentFamilyError,
    ExperimentLock,
    H0Freeze,
    Hyperparams,
    ModelSpec,
    sha256_file,
    sha256_text,
)
from variant_pool.gate import DEFAULT_MIN_FORK
from variant_pool.ledger import DEFAULT_STALE_PRIOR
from variant_pool.pool import DEFAULT_K
from variant_pool.router import CLUSTER_MODES, TIE_BREAKS

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _lock(**overrides) -> ExperimentLock:
    base = dict(
        experiment_id="M0-global-s0",
        created_at="2026-07-23T12:00:00Z",
        git_sha="f7fb6dd",
        h0=H0Freeze(
            config_sha256="a" * 64,
            system_prompt_sha256="b" * 64,
            tool_registry=("WebSearch", "WebFetch", "PythonExec"),
        ),
        models=ModelSpec(
            task_agent_model="deepseek/deepseek-v4-flash",
            meta_agent_model="deepseek/deepseek-v4-pro",
            api_base="https://api.deepseek.com",
        ),
        dataset=DatasetSpec(
            path="recipe/gaia_evolver/data/webthinker_gaia_dev.json",
            sha256="c" * 64,
            size=103,
            level_distribution=dict(PAPER_LEVEL_DISTRIBUTION),
        ),
    )
    base.update(overrides)
    return ExperimentLock(**base)


def _write_tasks(tmp_path, counts: dict[int, int]):
    rows = [
        {"task_id": f"t{level}-{i}", "Question": "q", "answer": "a", "Level": level}
        for level, n in sorted(counts.items())
        for i in range(n)
    ]
    path = tmp_path / "webthinker_gaia_dev.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ===========================================================================
# The §6.6 contract table — every blank has a recorded default
# ===========================================================================


def test_every_blank_of_the_contract_table_is_locked() -> None:
    """SPEC §6.6: each row is an explicit experiment choice, so each is a field."""
    required = {
        "K",
        "estimator",
        "window",
        "stale_prior",
        "cold_start",
        "tie_break",
        "epsilon",
        "fork_inheritance",
        "min_fork",
        "retirement_metric",
        "retire_reassign",
        "target_strategy",
        "target_strategy_provenance",
        "idle_scope",
        "cluster_mode",
        "cluster_source",
        "routing_mode",
        "routing_window",
        "candidates_per_round",
        "candidate_mode",
        "candidate_limit",
        "candidate_pipeline_adapter",
        "candidate_pipeline_semantics",
        "actionability_threshold",
        "actionability_threshold_provenance",
        "baseline_round_policy",
        "per_variant_persistence",
        "multi_bucket_attribution",
        "hit_rate_scope",
        "level2_evidence_type",
    }
    assert required <= set(Hyperparams().__dataclass_fields__)


def test_defaults_agree_with_the_modules_they_describe() -> None:
    """A lock that disagreed with the code would document a run that never happened."""
    params = Hyperparams()
    assert params.K == DEFAULT_K
    assert params.min_fork == DEFAULT_MIN_FORK
    assert params.stale_prior == DEFAULT_STALE_PRIOR
    assert params.cluster_mode in CLUSTER_MODES
    assert params.tie_break in TIE_BREAKS
    assert params.target_strategy == "worst_first"
    assert params.target_strategy_provenance.startswith("OURS:")
    assert params.window is None  # full history (SPEC §6.6)
    assert params.epsilon == 0.0  # pure argmax


def test_runtime_parameters_and_paper_plan_are_not_conflated() -> None:
    """A single-run lock separates enabled behavior from Table-8 plans."""
    params = Hyperparams()
    assert params.candidate_mode == "paper"
    assert params.candidates_per_round == "global_up_to_4"
    assert params.candidate_limit == 4
    assert "not_full_llm_aegis" in params.candidate_pipeline_semantics
    assert params.actionability_threshold == 1.0
    assert params.baseline_round_policy.startswith("R0_")
    assert (params.T, params.P) == (15, 3)
    assert params.pass_at_k == 2
    assert (params.max_steps, params.concurrency) == (20, 10)
    assert (params.meta_concurrency, params.meta_max_steps) == (4, 200)
    assert params.noise_threshold is None
    assert params.planned_candidates_per_round == 4
    assert params.planned_meta_concurrency == 4
    assert params.planned_noise_threshold == 0.05
    assert len(params.planned_seeds) == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"K": 0},
        {"planned_candidates_per_round": 0},
        {"candidate_limit": 0},
        {"candidate_limit": 5},
        {"actionability_threshold": -0.1},
        {"pass_at_k": 0},
        {"epsilon": 1.5},
        {"stale_prior": -0.1},
    ],
)
def test_impossible_hyperparameters_are_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        Hyperparams(**kwargs)


# ===========================================================================
# Serialisation
# ===========================================================================


def test_json_round_trip_restores_every_type(tmp_path) -> None:
    """JSON has no tuples and no integer keys; the lock puts both back."""
    original = _lock()
    again = ExperimentLock.from_json(original.to_json())

    assert again == original
    assert isinstance(again.h0.tool_registry, tuple)
    assert isinstance(again.hyperparams.min_fork, tuple)
    assert isinstance(again.hyperparams.planned_seeds, tuple)
    assert again.dataset.level_distribution == PAPER_LEVEL_DISTRIBUTION
    assert all(isinstance(key, int) for key in again.dataset.level_distribution)


def test_lock_lands_on_disk_under_the_spec_name(tmp_path) -> None:
    path = _lock().save(tmp_path / "runs" / "m0")
    assert path.name == LOCK_FILENAME
    assert ExperimentLock.load(path) == _lock()
    assert ExperimentLock.load(path.parent) == _lock()  # directory also works


def test_an_unknown_field_is_a_hard_error() -> None:
    """A lock with a field this schema does not know is a lock from another schema."""
    payload = json.loads(_lock().to_json())
    payload["hyperparams"]["temperature"] = 0.7
    with pytest.raises(ValueError, match="temperature"):
        ExperimentLock.from_json(json.dumps(payload))


def test_legacy_lock_reads_with_plans_migrated_from_runtime_fields() -> None:
    payload = json.loads(_lock().to_json())
    hyper = payload["hyperparams"]
    hyper["K_t"] = hyper.pop("planned_candidates_per_round")
    hyper["seeds"] = hyper.pop("planned_seeds")
    hyper["retire_metric"] = hyper.pop("retirement_metric")
    hyper.pop("candidates_per_round")
    hyper.pop("planned_meta_concurrency")
    hyper.pop("planned_noise_threshold")
    hyper.pop("routing_mode")
    hyper.pop("routing_window")
    hyper.pop("cluster_source")

    migrated = ExperimentLock.from_json(json.dumps(payload))
    assert migrated.hyperparams.candidates_per_round == "one_per_active_variant"
    assert migrated.hyperparams.planned_candidates_per_round == 4
    assert migrated.hyperparams.planned_seeds == (0, 1, 2)
    assert migrated.hyperparams.target_strategy == "all_active_variants"
    assert migrated.hyperparams.routing_mode == "task_tournament"
    assert any("legacy lock migrated" in warning for warning in migrated.provenance_warnings)


def test_spec_version_travels_with_the_lock() -> None:
    assert _lock().spec_version == SPEC_VERSION
    assert json.loads(_lock().to_json())["spec_version"] == SPEC_VERSION


# ===========================================================================
# Fingerprint
# ===========================================================================


def test_sha256_is_stable_across_key_order_and_reserialisation() -> None:
    lock = _lock()
    assert lock.sha256() == ExperimentLock.from_json(lock.to_json()).sha256()
    # a differently-ordered but identical mapping hashes the same
    shuffled = ExperimentLock(
        created_at=lock.created_at,
        experiment_id=lock.experiment_id,
        env=lock.env,
        hyperparams=lock.hyperparams,
        dataset=lock.dataset,
        models=lock.models,
        h0=lock.h0,
        git_sha=lock.git_sha,
    )
    assert shuffled.sha256() == lock.sha256()


def test_any_recorded_change_moves_the_fingerprint() -> None:
    baseline = _lock().sha256()
    assert _lock(hyperparams=Hyperparams(K=16)).sha256() != baseline
    assert _lock(env=EnvSpec(seed=7)).sha256() != baseline
    assert _lock(experiment_id="M0-global-s1").sha256() != baseline  # identity counts too


def test_content_hash_helpers() -> None:
    assert sha256_text("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_file_hash_helper(tmp_path) -> None:
    path = tmp_path / "prompt.txt"
    path.write_text("you are a competent harness", encoding="utf-8")
    assert sha256_file(path) == sha256_text("you are a competent harness")


# ===========================================================================
# diff
# ===========================================================================


def test_diff_is_empty_for_identical_locks() -> None:
    assert _lock().diff(_lock()) == []


def test_diff_reports_dotted_paths_sorted() -> None:
    changed = _lock(
        hyperparams=Hyperparams(K=16, epsilon=0.1),
        env=EnvSpec(seed=7),
    )
    entries = _lock().diff(changed)
    assert entries == sorted(entries)
    assert "env.seed: 0 -> 7" in entries
    assert "hyperparams.K: 8 -> 16" in entries
    assert "hyperparams.epsilon: 0.0 -> 0.1" in entries


def test_diff_sees_a_tool_leaving_the_registry() -> None:
    """A registry difference must name the tool — A.4 pins the registry to H0."""
    stripped = _lock(h0=H0Freeze("a" * 64, "b" * 64, ("WebSearch", "WebFetch")))
    entries = _lock().diff(stripped)
    assert len(entries) == 1
    assert "h0.tool_registry" in entries[0]
    assert "PythonExec" in entries[0]


# ===========================================================================
# assert_same_family — SPEC §6.3
# ===========================================================================


def test_family_sections_are_h0_models_and_data() -> None:
    assert FAMILY_SECTIONS == ("h0", "models", "dataset")


def test_hyperparameter_changes_are_ablations_not_new_families() -> None:
    """SPEC §6.6: the blanks are the contribution surface, so they must move freely."""
    arm = _lock(hyperparams=Hyperparams(K=16, cluster_mode="failure", epsilon=0.1))
    assert _lock().same_family(arm) is True
    _lock().assert_same_family(arm)  # does not raise


def test_env_and_identity_changes_are_not_new_families() -> None:
    other_seed = _lock(experiment_id="M0-global-s2", env=EnvSpec(seed=2), created_at="2026-07-24T00:00:00Z")
    _lock().assert_same_family(other_seed)


def test_changing_the_frozen_h0_opens_a_new_family() -> None:
    """SPEC §6.3 verbatim: H0 frozen once; a change opens a new experiment family."""
    mutated = _lock(h0=H0Freeze("d" * 64, "b" * 64, ("WebSearch", "WebFetch", "PythonExec")))
    assert _lock().same_family(mutated) is False
    with pytest.raises(ExperimentFamilyError, match="h0.config_sha256"):
        _lock().assert_same_family(mutated)


def test_changing_the_system_prompt_or_registry_opens_a_new_family() -> None:
    prompt = _lock(h0=H0Freeze("a" * 64, "e" * 64, ("WebSearch", "WebFetch", "PythonExec")))
    registry = _lock(h0=H0Freeze("a" * 64, "b" * 64, ("WebSearch",)))
    for mutated in (prompt, registry):
        with pytest.raises(ExperimentFamilyError):
            _lock().assert_same_family(mutated)


def test_swapping_a_model_opens_a_new_family() -> None:
    """The DeepSeek substitution is why absolute scores are not comparable (SPEC preamble)."""
    mutated = _lock(
        models=ModelSpec(
            task_agent_model="deepseek/deepseek-v4-pro",
            meta_agent_model="deepseek/deepseek-v4-pro",
            api_base="https://api.deepseek.com",
        )
    )
    with pytest.raises(ExperimentFamilyError, match="models.task_agent_model"):
        _lock().assert_same_family(mutated)


def test_a_re_pulled_dataset_opens_a_new_family() -> None:
    mutated = _lock(dataset=DatasetSpec(path="x.json", sha256="f" * 64, size=103))
    with pytest.raises(ExperimentFamilyError, match="dataset"):
        _lock().assert_same_family(mutated)


def test_the_error_lists_every_offending_field() -> None:
    mutated = _lock(
        h0=H0Freeze("d" * 64, "e" * 64, ()),
        hyperparams=Hyperparams(K=16),
    )
    with pytest.raises(ExperimentFamilyError) as excinfo:
        _lock().assert_same_family(mutated)
    message = str(excinfo.value)
    assert "h0.config_sha256" in message
    assert "h0.system_prompt_sha256" in message
    assert "hyperparams.K" not in message  # an ablation is not an offence


# ===========================================================================
# DatasetSpec.from_file
# ===========================================================================


def test_dataset_spec_summarises_the_task_file(tmp_path) -> None:
    path = _write_tasks(tmp_path, PAPER_LEVEL_DISTRIBUTION)
    spec = DatasetSpec.from_file(path)

    assert spec.size == 103
    assert spec.level_distribution == PAPER_LEVEL_DISTRIBUTION
    assert spec.matches_paper_levels() is True
    assert spec.sha256 == sha256_file(path)


def test_a_mis_stratified_subset_is_visible_in_the_lock(tmp_path) -> None:
    """A.2 p.28 is 39/52/12; a lock that records the mix cannot hide a skewed one."""
    path = _write_tasks(tmp_path, {1: 50, 2: 50, 3: 3})
    spec = DatasetSpec.from_file(path)
    assert spec.matches_paper_levels() is False
    assert spec.level_distribution == {1: 50, 2: 50, 3: 3}


def test_dataset_hash_changes_with_content(tmp_path) -> None:
    first = DatasetSpec.from_file(_write_tasks(tmp_path, {1: 2, 2: 3, 3: 1}))
    second = DatasetSpec.from_file(_write_tasks(tmp_path, {1: 2, 2: 3, 3: 2}))
    assert first.sha256 != second.sha256


def test_a_non_array_task_file_is_rejected(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"task_id": "t"}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        DatasetSpec.from_file(path)
