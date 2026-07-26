# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Algorithm 1's alpha: mode-dependent auto default, explicit flag wins.

runs/a1smoke: the LLM Digester's real-valued a_t=0.9 met the legacy implicit
default alpha=1.0 (calibrated for the binary fallback digester) and silently
skipped the whole round. The resolver keeps deterministic mode byte-identical
(1.0) and defaults llm mode to the library's OURS value (0.5).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    OURS_DEFAULT_ACTIONABILITY_THRESHOLD,
)


def test_deterministic_mode_defaults_to_legacy_1_0() -> None:
    assert rvp._resolve_actionability_threshold(None, "deterministic") == 1.0


def test_llm_mode_defaults_to_the_library_ours_value() -> None:
    resolved = rvp._resolve_actionability_threshold(None, "llm")
    assert resolved == OURS_DEFAULT_ACTIONABILITY_THRESHOLD == 0.5


def test_explicit_value_wins_in_both_modes() -> None:
    assert rvp._resolve_actionability_threshold(0.7, "deterministic") == 0.7
    assert rvp._resolve_actionability_threshold(0.7, "llm") == 0.7


def test_parser_default_is_none_and_registered_exactly_once() -> None:
    # runs/paper4 crash class: a duplicate registration (argparse conflict) or
    # a re-added explicit default would silently disable the auto semantics.
    parser = rvp.build_arg_parser()
    assert parser.get_default("actionability_threshold") is None


def test_no_consumer_bypasses_the_resolver() -> None:
    # runs/paper4 crashed on a leftover ``float(getattr(args,
    # "actionability_threshold", 1.0))`` in _build_experiment_lock: float(None)
    # after the default moved to None. Every consumer must go through
    # _resolve_actionability_threshold (or the recipe's resolved attribute).
    import re

    src = Path(rvp.__file__).read_text(encoding="utf-8")
    raw_consumers = re.findall(
        r'float\(\s*getattr\((?:self\.)?args,\s*"actionability_threshold"', src
    )
    assert raw_consumers == []
    assert src.count('"--actionability-threshold"') == 1


def test_paper_brief_spells_out_the_capability_evidence_type_vocabulary() -> None:
    # runs/paper3: both metas invented types (search_backend,
    # trajectory_analysis) because the brief never gave the enum.
    brief = rvp.PAPER_MANIFEST_SCHEMA_BRIEF
    assert "python_package, http_endpoint, builtin_tool, filesystem, other" in brief
    assert "use 'other' when unsure" in brief
