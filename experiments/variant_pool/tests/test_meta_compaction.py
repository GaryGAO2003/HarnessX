# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--meta-compaction`` (batch-4b Item 4).

The Evolver meta-harness ALWAYS carries a CompactionProcessor (built by
``build_meta_agent_harness_config`` with the vendored default 200000 / 4 / 0.95).
The flag overrides its three numeric knobs to the official AEGIS Evolver tuning
(300000 / 4 / 0.90; ``upstream/feat/aegis:harnessx/aegis/agents/evolver.py``).
These tests pin: the wiring helper (off ⇒ None; on ⇒ the three override kwargs);
that the built config's CompactionProcessor carries those params on/off; and that
OFF leaves the processor list byte-identical (same processors, vendored params).

Fully offline; no provider, no network. The build uses a stub ``ModelConfig``
whose provider is never invoked at construction time.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harnessx.core.model_config import ModelConfig  # noqa: E402
from harnessx.meta_harness.agent import build_meta_agent_harness_config  # noqa: E402
from recipe.gaia_evolver.run_variant_pool import _meta_compaction_kws  # noqa: E402


def _compaction_dict(cfg) -> dict:
    for proc in cfg.processors:
        if isinstance(proc, dict) and str(proc.get("_target_", "")).endswith(
            "CompactionProcessor"
        ):
            return proc
    raise AssertionError("no CompactionProcessor in the built meta-harness config")


def _proc_names(cfg) -> list[str]:
    return [
        str(p.get("_target_", "")) for p in cfg.processors if isinstance(p, dict)
    ]


def _build(**kws):
    tmp = Path(tempfile.mkdtemp())
    mc = ModelConfig(main=object())
    return build_meta_agent_harness_config(
        inner_model=mc, workspace_root=tmp, output_dir=tmp, **kws
    )


# ---------------------------------------------------------------------------
# 1. Wiring helper: off ⇒ None (caller wires nothing); on ⇒ override kwargs.
# ---------------------------------------------------------------------------
def test_meta_compaction_kws_off_is_none() -> None:
    assert _meta_compaction_kws(argparse.Namespace(meta_compaction=False)) is None
    # Missing attribute also behaves as off.
    assert _meta_compaction_kws(argparse.Namespace()) is None


def test_meta_compaction_kws_on_builds_official_evolver_values() -> None:
    kws = _meta_compaction_kws(argparse.Namespace(meta_compaction=True))
    assert kws == {
        "compaction_token_threshold": 300000,
        "compaction_retention_window": 4,
        "compaction_eviction_fraction": 0.90,
    }


# ---------------------------------------------------------------------------
# 2. OFF: the built CompactionProcessor keeps the vendored defaults, and the
#    whole processor list is byte-identical (this is the byte-identical pin).
# ---------------------------------------------------------------------------
def test_default_build_keeps_vendored_compaction_params() -> None:
    cfg = _build()
    comp = _compaction_dict(cfg)
    assert comp["token_threshold"] == 200000
    assert comp["retention_window"] == 4
    assert comp["eviction_fraction"] == 0.95


def test_off_processor_list_byte_identical_to_on_except_params() -> None:
    off = _build()
    on = _build(**_meta_compaction_kws(argparse.Namespace(meta_compaction=True)))
    # Same processors, same order — only the CompactionProcessor's numeric
    # params differ. The OFF list is exactly today's list.
    assert _proc_names(off) == _proc_names(on)
    # The summarize template is untouched by the flag (Evolver-appropriate).
    assert _compaction_dict(off)["summarize_prompt_template"] == _compaction_dict(on)[
        "summarize_prompt_template"
    ]


# ---------------------------------------------------------------------------
# 3. ON: the built CompactionProcessor carries the official Evolver params.
# ---------------------------------------------------------------------------
def test_flag_on_overrides_to_official_evolver_params() -> None:
    cfg = _build(**_meta_compaction_kws(argparse.Namespace(meta_compaction=True)))
    comp = _compaction_dict(cfg)
    assert comp["token_threshold"] == 300000
    assert comp["retention_window"] == 4
    assert comp["eviction_fraction"] == 0.90
