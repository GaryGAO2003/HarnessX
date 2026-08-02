"""``--cluster-source`` / ``--cluster-min-size`` / ``--epsilon`` (M-34).

Why this exists. SS4.5 routes each task to ``argmax_v S_hat(v, cluster(task))``
and never defines ``cluster``; ``Router.cluster_of`` says so in its own
NotImplementedError. The choice is structural rather than cosmetic: routing is
argmax **per cluster**, so at most ``min(K, n_clusters)`` variants can ever carry
tasks. Under ``gaia_level`` GAIA has three clusters, which caps an eight-variant
pool at three loaded variants no matter what the gate does -- measured on
s1k8b103, final load ``[52, 39, 12, 0, 0, 0, 0, 0]``.

The first test is the load-bearing one: the default must reproduce the previous
hardcoded partition exactly, or every run to date becomes incomparable.
"""
import argparse
import hashlib
import json

import pytest

from recipe.gaia_evolver.run_variant_pool import (
    CLUSTER_SOURCES,
    DEFAULT_CLUSTER_MIN_SIZE,
    DEFAULT_CLUSTER_SOURCE,
    _cluster_source_provenance,
    _epsilon_provenance,
    _merge_small_clusters,
    _resolve_task_clusters,
    build_arg_parser,
)

LEVELS = {f"t{i:02d}": (1 if i < 4 else 2 if i < 9 else 3) for i in range(12)}


def _args(**over):
    base = dict(
        cluster_source=DEFAULT_CLUSTER_SOURCE,
        cluster_map=None,
        cluster_min_size=DEFAULT_CLUSTER_MIN_SIZE,
        epsilon=0.0,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _write_map(tmp_path, clusters, name="m.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"clusters": clusters}), encoding="utf-8")
    return p


# --- the default must not move -------------------------------------------


def test_default_reproduces_the_previous_hardcoded_partition():
    got, prov = _resolve_task_clusters(_args(), LEVELS)
    assert got == {tid: f"gaia_level_{lvl}" for tid, lvl in LEVELS.items()}
    assert prov["cluster_source"] == "gaia_level"
    assert prov["cluster_map_sha256"] is None


def test_default_partition_has_exactly_three_clusters_which_is_the_ceiling():
    got, _ = _resolve_task_clusters(_args(), LEVELS)
    # This is the finding, asserted so a future edit cannot quietly change it:
    # three clusters cap a K=8 pool at three loaded variants.
    assert len(set(got.values())) == 3


def test_default_ignores_a_map_that_happens_to_be_present(tmp_path):
    path = _write_map(tmp_path, {tid: "search+compute" for tid in LEVELS})
    got, prov = _resolve_task_clusters(_args(cluster_map=str(path)), LEVELS)
    assert len(set(got.values())) == 3
    assert prov["cluster_map_path"] is None


# --- capability partition -------------------------------------------------


def test_capability_reads_the_frozen_map_and_records_its_digest(tmp_path):
    table = {tid: ("search+compute" if lvl < 3 else "browse+verify") for tid, lvl in LEVELS.items()}
    path = _write_map(tmp_path, table)
    got, prov = _resolve_task_clusters(
        _args(cluster_source="capability", cluster_map=str(path), cluster_min_size=1), LEVELS
    )
    assert got == table
    assert prov["cluster_source"] == "capability"
    assert prov["cluster_map_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert prov["cluster_count"] == 2


def test_capability_without_a_map_is_refused():
    with pytest.raises(SystemExit, match="requires --cluster-map"):
        _resolve_task_clusters(_args(cluster_source="capability"), LEVELS)


def test_a_missing_map_file_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        _resolve_task_clusters(
            _args(cluster_source="capability", cluster_map=str(tmp_path / "nope.json")), LEVELS
        )


def test_partial_coverage_fails_closed_rather_than_cold_starting(tmp_path):
    # Letting an unmapped task fall through to cold-start would route the run on
    # a partition the frozen map does not describe, undetectably.
    table = {tid: "search" for tid in list(LEVELS)[:-2]}
    path = _write_map(tmp_path, table)
    with pytest.raises(SystemExit, match="missing 2 of 12"):
        _resolve_task_clusters(
            _args(cluster_source="capability", cluster_map=str(path)), LEVELS
        )


def test_an_empty_clusters_object_is_refused(tmp_path):
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"clusters": {}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="no non-empty"):
        _resolve_task_clusters(
            _args(cluster_source="capability", cluster_map=str(path)), LEVELS
        )


def test_an_unknown_source_is_refused():
    with pytest.raises(SystemExit, match="--cluster-source must be one of"):
        _resolve_task_clusters(_args(cluster_source="kmeans"), LEVELS)


# --- small-cluster merging ------------------------------------------------


def test_small_clusters_join_the_most_similar_large_one_not_a_misc_bucket():
    assignment = {f"a{i}": "browse+compute+search+verify" for i in range(10)}
    assignment.update({f"b{i}": "compute+search+verify" for i in range(10)})
    assignment["s1"] = "browse+search+verify"  # Jaccard .75 with the first
    assignment["s2"] = "compute+verify"  # Jaccard .67 with the second
    merged, into = _merge_small_clusters(assignment, 8)
    # `into` is keyed by cluster id, not task id.
    assert into["browse+search+verify"] == "browse+compute+search+verify"
    assert into["compute+verify"] == "compute+search+verify"
    assert merged["s1"] == "browse+compute+search+verify"
    assert merged["s2"] == "compute+search+verify"
    assert len(set(merged.values())) == 2


def test_merging_is_deterministic_under_a_similarity_tie():
    assignment = {f"a{i}": "search" for i in range(10)}
    assignment.update({f"b{i}": "compute" for i in range(10)})
    assignment["s"] = "verify"  # Jaccard 0 with both -> tie, break on size then id
    first = _merge_small_clusters(assignment, 8)[1]["verify"]
    for _ in range(5):
        assert _merge_small_clusters(dict(assignment), 8)[1]["verify"] == first


def test_an_all_small_partition_is_left_alone_rather_than_collapsed():
    assignment = {f"t{i}": f"c{i}" for i in range(6)}
    merged, into = _merge_small_clusters(assignment, 8)
    assert merged == assignment and into == {}


def test_min_size_one_disables_merging(tmp_path):
    table = {tid: f"c{i}" for i, tid in enumerate(LEVELS)}
    path = _write_map(tmp_path, table)
    got, prov = _resolve_task_clusters(
        _args(cluster_source="capability", cluster_map=str(path), cluster_min_size=1), LEVELS
    )
    assert got == table
    assert prov["cluster_count"] == len(LEVELS)


# --- provenance -----------------------------------------------------------


def test_default_source_and_epsilon_emit_no_provenance_warning():
    assert _cluster_source_provenance(_args()) is None
    assert _epsilon_provenance(0.0) is None


def test_capability_warning_carries_the_digest_and_the_min_size(tmp_path):
    path = _write_map(tmp_path, {tid: "search" for tid in LEVELS})
    warn = _cluster_source_provenance(
        _args(cluster_source="capability", cluster_map=str(path), cluster_min_size=8)
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() in warn
    assert "cluster-min-size=8" in warn
    assert "min(K, n_clusters)" in warn


def test_epsilon_warning_states_the_accuracy_cost():
    warn = _epsilon_provenance(0.1)
    assert "accuracy cost proportional to epsilon" in warn
    assert "cannot win" in warn


# --- CLI ------------------------------------------------------------------


def test_cli_defaults_are_the_pre_flag_behaviour():
    a = build_arg_parser().parse_args(["--provider-id", "deepseek"])
    assert a.cluster_source == "gaia_level"
    assert a.cluster_map is None
    assert a.epsilon == 0.0
    assert a.cluster_min_size == DEFAULT_CLUSTER_MIN_SIZE


def test_cli_accepts_the_capability_arm():
    a = build_arg_parser().parse_args(
        ["--provider-id", "deepseek", "--cluster-source", "capability",
         "--cluster-map", "m.json", "--epsilon", "0.1", "--cluster-min-size", "10"]
    )
    assert (a.cluster_source, a.cluster_map, a.epsilon, a.cluster_min_size) == (
        "capability", "m.json", 0.1, 10
    )


def test_cli_rejects_an_unknown_source():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--provider-id", "deepseek", "--cluster-source", "kmeans"])


def test_declared_sources_match_the_cli_choices():
    assert CLUSTER_SOURCES == ("gaia_level", "capability")
