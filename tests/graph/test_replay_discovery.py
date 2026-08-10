"""Replay corpus discovery — every layout with a candidate id in the path.

Regression for the full-corpus replay undercount (98 vs 133): configs nested
below the candidate dir (``C-*/output_dir/config.yaml``, s1k8b103 layout)
were invisible to the old ``rglob("C-*/config.yaml")`` pattern.
"""

from pathlib import Path

from experiments.analysis.replay_validator import discover_candidates


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("processors: []\n", encoding="utf-8")


def test_discovers_direct_and_nested_layouts(tmp_path):
    _touch(tmp_path / "R1" / "V0" / "candidate_gate" / "C-R1-01" / "config.yaml")
    _touch(tmp_path / "R1" / "V0" / "pipeline" / "candidates" / "C-R1-02"
           / "output_dir" / "config.yaml")           # nested: old pattern missed it
    found = discover_candidates(tmp_path)
    assert set(found) == {"C-R1-01", "C-R1-02"}


def test_shortest_path_wins_per_id(tmp_path):
    deep = tmp_path / "R2" / "V0" / "pipeline" / "candidates" / "C-R2-01" \
        / "retry" / "config.yaml"
    canonical = tmp_path / "R2" / "C-R2-01" / "config.yaml"
    _touch(deep)
    _touch(canonical)
    found = discover_candidates(tmp_path)
    assert found["C-R2-01"] == canonical


def test_non_candidate_configs_skipped(tmp_path):
    _touch(tmp_path / "V0" / "config.yaml")          # parent/base config: no C-R id
    _touch(tmp_path / "R1" / "C-R1-01" / "config.yaml")
    found = discover_candidates(tmp_path)
    assert set(found) == {"C-R1-01"}
