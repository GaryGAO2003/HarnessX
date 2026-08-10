"""G1 gate — historical-config roundtrip runner (graph→config→build→re-graph).

Covers ``experiments.analysis.roundtrip_validator``: the always-green builtin
baseline, the genotype-equality criterion the runner adds on top of
``transactional_apply``, the environment-fair classification (import failures
excluded from the headline), and the per-run candidate-id discovery scoping.

Zero API / zero network — every config is built or loaded in-process.
"""

import copy

from omegaconf import OmegaConf

from harnessx.core.builder import HarnessBuilder, build_from_config
from harnessx.core.harness import HarnessConfig
from harnessx.graph.snapshot import to_graph
from harnessx.graph.transform import graph_to_config_dict

from experiments.analysis import roundtrip_validator as rv
from experiments.analysis.roundtrip_validator import (
    IMPORT_UNCERTAIN,
    LOAD_FAILED,
    MISMATCH,
    MISMATCH_DIVERGENT,
    MISMATCH_STABILIZES,
    OK,
    _builtin_recipes,
    discover_yaml_configs,
    roundtrip_config,
    run,
)

_COST_GUARD = "harnessx.processors.control.cost_guard.CostGuardProcessor"


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OmegaConf.to_yaml(OmegaConf.create(data)), encoding="utf-8")


def _full_metadata_config_dict():
    """A fully v5.3-serialized single-processor config (instance-introspected)."""
    inst = build_from_config({"processors": [{"_target_": _COST_GUARD}]})
    return graph_to_config_dict(to_graph(inst))


# ── builtin baseline ─────────────────────────────────────────────────────────


def test_builtin_corpus_all_roundtrip_ok():
    """Every builtin composition round-trips genotype-stably (100%, no yaml)."""
    result = run([])
    builtins = {k: v for k, v in result["per_item"].items()
                if v["source"] == "builtin"}
    assert builtins, "no builtin corpus items were produced"
    assert result["by_source"].get("builtin") == len(_builtin_recipes())
    offenders = {k: v["verdict"] for k, v in builtins.items()
                 if v["verdict"] != OK}
    assert not offenders, offenders
    # with no on-disk corpus both headlines are a clean 100%
    assert result["headline"]["roundtrip_rate"] == 1.0
    assert result["headline"]["fixed_point_rate"] == 1.0
    assert result["headline"]["mismatch_stabilizes"] == 0
    assert result["headline"]["build_import_uncertain_excluded"] == 0


# ── genotype-equality criterion ──────────────────────────────────────────────


def test_valid_full_metadata_yaml_roundtrips_ok(tmp_path):
    """A well-formed config with an importable real target → roundtrip_ok."""
    p = tmp_path / "config.yaml"
    _write_yaml(p, _full_metadata_config_dict())
    cfg = HarnessConfig.from_yaml_file(str(p))
    verdict, detail = roundtrip_config(cfg)
    assert verdict == OK, detail
    assert detail["genotype"]


def test_partial_metadata_yaml_is_hash_mismatch(tmp_path):
    """Old-format serialization (no _order_/_singleton_group_/_hooks_/…) is a
    real FIRST-loop genotype mismatch: dict metadata (g1) vs instance
    introspection (g2).  transactional_apply passes; the runner's explicit
    assertion catches the drift.  But g2 already carries full metadata, so the
    SECOND normalization loop reaches a fixed point (g3 == g2) — the mismatch is
    sub-classified ``mismatch_stabilizes``, not divergence.
    """
    partial = {"processors": [{
        "_target_": _COST_GUARD, "_code_hash": "sha256:deadbeef",
        "max_usd": 8.0, "warning_threshold": 0.8, "_hook_": "*",
    }]}
    p = tmp_path / "config.yaml"
    _write_yaml(p, partial)
    cfg = HarnessConfig.from_yaml_file(str(p))
    verdict, detail = roundtrip_config(cfg)
    assert verdict == MISMATCH, detail
    assert detail["subclass"] == MISMATCH_STABILIZES, detail
    # g1 (partial) drifts to g2 (full introspection); g3 settles onto g2
    assert detail["genotype_g1"] != detail["genotype_g2"]
    assert detail["genotype_g3"] == detail["genotype_g2"]


def test_hash_mismatch_via_regraph_seam(monkeypatch):
    """The classification path itself: a re-graph seam that distorts DIFFERENTLY
    on every call (leaving transactional_apply untouched) never reaches a fixed
    point → MISMATCH / ``mismatch_divergent`` with all three genotypes distinct.
    """
    from harnessx.bundles import context
    cfg = (HarnessBuilder() | context).build()

    calls = {"n": 0}

    def _distort(snapshot):
        distorted = copy.deepcopy(snapshot)
        node = next(iter(distorted.nodes.values()))
        calls["n"] += 1
        node.metadata["_roundtrip_test_distortion_"] = calls["n"]  # differs/call
        return distorted

    monkeypatch.setattr(rv, "_materialize_and_regraph", _distort)
    verdict, detail = roundtrip_config(cfg)
    assert verdict == MISMATCH, detail
    assert detail["subclass"] == MISMATCH_DIVERGENT, detail
    # loop 1 → g2 (n=1) != g1; loop 2 → g3 (n=2) != g2
    assert detail["genotype_g1"] != detail["genotype_g2"]
    assert detail["genotype_g3"] != detail["genotype_g2"]


def test_second_loop_build_failure_is_divergent(monkeypatch):
    """A second-loop build failure (no reachable fixed point) is recorded as
    ``mismatch_divergent`` with its error, never an unhandled crash.
    """
    from harnessx.bundles import context
    cfg = (HarnessBuilder() | context).build()

    calls = {"n": 0}

    def _flaky(snapshot):
        calls["n"] += 1
        if calls["n"] == 1:  # loop 1: distort so h1 != h2
            distorted = copy.deepcopy(snapshot)
            node = next(iter(distorted.nodes.values()))
            node.metadata["_roundtrip_test_distortion_"] = 1
            return distorted
        raise RuntimeError("second-loop build boom")  # loop 2: build blows up

    monkeypatch.setattr(rv, "_materialize_and_regraph", _flaky)
    verdict, detail = roundtrip_config(cfg)
    assert verdict == MISMATCH, detail
    assert detail["subclass"] == MISMATCH_DIVERGENT, detail
    assert detail["stage"] == "regraph2"
    assert "RuntimeError" in detail["error"]


# ── environment-fair classification ──────────────────────────────────────────


def test_missing_module_is_import_uncertain_and_excluded(tmp_path):
    """A target that fails to import is build_import_uncertain and drops out of
    the headline denominator; everything else stays counted.
    """
    _write_yaml(tmp_path / "good" / "config.yaml", _full_metadata_config_dict())
    _write_yaml(tmp_path / "bad" / "config.yaml", {"processors": [{
        "_target_": "nonexistent.totally.MadeUpProcessor",
        "_hook_": "*", "_singleton_group_": "made_up",
    }]})

    result = run([tmp_path])
    bad = next(v for k, v in result["per_item"].items()
               if k.endswith("bad/config.yaml"))
    good = next(v for k, v in result["per_item"].items()
                if k.endswith("good/config.yaml"))
    assert bad["verdict"] == IMPORT_UNCERTAIN, bad
    assert good["verdict"] == OK, good

    h = result["headline"]
    assert h["build_import_uncertain_excluded"] >= 1
    assert h["denominator"] == result["total"] - h["build_import_uncertain_excluded"]
    # here every non-import item is ok → both headlines are a clean 100%
    assert h["roundtrip_rate"] == 1.0
    assert h["fixed_point_rate"] == 1.0


def test_config_load_failed(tmp_path):
    """A config the schema cannot load is classified config_load_failed."""
    p = tmp_path / "config.yaml"
    p.write_text("processors: 5\n", encoding="utf-8")  # processors must be a list
    result = run([tmp_path])
    row = next(v for k, v in result["per_item"].items()
               if v["source"] == "yaml")
    assert row["verdict"] == LOAD_FAILED, row


# ── discovery ────────────────────────────────────────────────────────────────


def test_discovery_scopes_candidate_id_per_run_and_shortest_wins(tmp_path):
    """C-* candidates dedup per run (shortest path wins); the same id in two
    runs stays distinct; non-candidate base configs are included.
    """
    def _touch(rel):
        fp = tmp_path / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text("processors: []\n", encoding="utf-8")
        return fp

    _touch("runA/R1/V0/pipeline/candidates/C-R1-01/config.yaml")     # deeper
    short = _touch("runA/R1/V0/candidate_gate/C-R1-01/config.yaml")  # shorter
    _touch("runB/R1/V0/pipeline/candidates/C-R1-01/config.yaml")     # other run
    _touch("runA/V0/config.yaml")                                    # base config

    items = discover_yaml_configs(tmp_path)
    assert "runA/C-R1-01" in items
    assert "runB/C-R1-01" in items            # not collapsed across runs
    assert items["runA/C-R1-01"] == short     # shortest path wins
    assert "runA/V0/config.yaml" in items     # non-candidate base included
