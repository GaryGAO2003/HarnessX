# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for batch-2a Item 4 — ``--digest-anchor-check``.

Fully offline (no LLM, no network). Ports the official IV-1 structure gate from
``upstream/feat/aegis:harnessx/aegis/gates/structure.py`` (``_ANCHOR_RE`` L32-39,
``validate_digest_anchors`` L76-113), adapted to our run layout and our bare-list
anchors. Default off is byte-identical.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from variant_pool.evidence import TaskDigest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402


# ---------------------------------------------------------------------------
# _parse_digest_anchors — the ported regex
# ---------------------------------------------------------------------------


def test_parse_finds_prefixed_paths_and_locators() -> None:
    assert rvp._parse_digest_anchors("trajectories/foo.jsonl#step_5") == [
        ("trajectories/foo.jsonl", "step_5")
    ]
    assert rvp._parse_digest_anchors("digests/x.md") == [("digests/x.md", None)]
    # optional round prefix + backtick wrapping (official accepts both forms).
    assert rvp._parse_digest_anchors("`R6/trajectories/f.jsonl#msg_2`") == [
        ("R6/trajectories/f.jsonl", "msg_2")
    ]


def test_parse_ignores_free_text_without_the_prefix() -> None:
    assert rvp._parse_digest_anchors("the model said the answer was 42") == []
    assert rvp._parse_digest_anchors("step 5 of the run") == []


# ---------------------------------------------------------------------------
# _validate_digest_anchors — the IV-1 checks
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, lines: int) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"line {i}" for i in range(lines)), encoding="utf-8")


def test_valid_anchor_within_line_count_passes(tmp_path) -> None:
    _write(tmp_path, "trajectories/t1.jsonl", lines=10)
    ok, _reason = rvp._validate_digest_anchors("trajectories/t1.jsonl#step_3", tmp_path)
    assert ok is True


def test_nonexistent_file_rejects(tmp_path) -> None:
    ok, reason = rvp._validate_digest_anchors("trajectories/ghost.jsonl#step_1", tmp_path)
    assert ok is False
    assert "missing" in reason


def test_out_of_range_locator_rejects(tmp_path) -> None:
    _write(tmp_path, "trajectories/t1.jsonl", lines=5)  # lines 0..4
    ok, reason = rvp._validate_digest_anchors("trajectories/t1.jsonl#step_9", tmp_path)
    assert ok is False
    assert "out of range" in reason


def test_no_anchors_passes_documented_fail_only_interaction(tmp_path) -> None:
    # Our fail_only prompt does not demand the strict format, so a digest with no
    # matching anchors passes (unlike the official IV-1 which requires >=1).
    ok, reason = rvp._validate_digest_anchors("just some free-form notes", tmp_path)
    assert ok is True
    assert "no citation anchors" in reason


def test_whole_file_digest_anchor_without_locator_passes(tmp_path) -> None:
    _write(tmp_path, "digests/d1.md", lines=3)
    ok, _reason = rvp._validate_digest_anchors("digests/d1.md", tmp_path)
    assert ok is True


# ---------------------------------------------------------------------------
# _check_digest_anchors on a constructed digester
# ---------------------------------------------------------------------------


def _digester(tmp_path: Path, **overrides) -> rvp._LLMDigester:
    kwargs = dict(
        evidence=None,
        pool=None,
        provider=None,
        tasks_by_id={},
        run_dir=tmp_path,
        fallback=None,
    )
    kwargs.update(overrides)
    return rvp._LLMDigester(**kwargs)


def test_check_digest_anchors_reads_evidence_anchors(tmp_path) -> None:
    _write(tmp_path, "trajectories/t1.jsonl", lines=8)
    dig = _digester(tmp_path)
    good = TaskDigest("t1", 1, "V0", (0, 2), evidence_anchors=["trajectories/t1.jsonl#step_2"])
    bad = TaskDigest("t2", 1, "V0", (0, 2), evidence_anchors=["trajectories/nope.jsonl#step_2"])
    assert dig._check_digest_anchors(good)[0] is True
    assert dig._check_digest_anchors(bad)[0] is False


# ---------------------------------------------------------------------------
# drop wiring through _interpret_task (one scripted LLM turn, no network)
# ---------------------------------------------------------------------------


class _OneShotProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def complete(self, messages, tools):  # noqa: ANN001, ARG002
        self.calls += 1
        return SimpleNamespace(content=self.content)


def _seeded_digest(tmp_path: Path) -> TaskDigest:
    # A real trajectory so _trajectory_window succeeds; the seed anchor resolves.
    (tmp_path / "trajectories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "trajectories" / "t1.md").write_text(
        "---\ntask_id: t1\n---\nline a\nline b\nline c\n", encoding="utf-8"
    )
    return TaskDigest("t1", 1, "V0", (0, 2), evidence_anchors=["trajectories/t1.md"])


def _json(anchor: str) -> str:
    return json.dumps(
        {
            "failure_category": "tool_effect_missing",
            "implicated_components": [],
            "evidence_anchors": [anchor],
            "notes": "",
        }
    )


def test_anchor_check_on_rejects_hallucinated_anchor(tmp_path) -> None:
    digest = _seeded_digest(tmp_path)
    dig = _digester(
        tmp_path,
        provider=_OneShotProvider(_json("trajectories/ghost.jsonl#step_9")),
        anchor_check=True,
    )
    returned, fb_note, anchor_note = asyncio.run(dig._interpret_task(None, digest, "ALL_FAIL"))
    # rejected -> deterministic digest kept (original failure_category is None).
    assert returned.failure_category is None
    assert fb_note is None
    assert anchor_note is not None and "anchor_check rejected" in anchor_note


def test_anchor_check_off_is_byte_identical_keeps_the_digest(tmp_path) -> None:
    digest = _seeded_digest(tmp_path)
    dig = _digester(
        tmp_path,
        provider=_OneShotProvider(_json("trajectories/ghost.jsonl#step_9")),
        anchor_check=False,  # default
    )
    returned, fb_note, anchor_note = asyncio.run(dig._interpret_task(None, digest, "ALL_FAIL"))
    # off: the mapped digest (with its hallucinated anchor) is kept, unchanged.
    assert returned.failure_category == "tool_effect_missing"
    assert "trajectories/ghost.jsonl#step_9" in returned.evidence_anchors
    assert fb_note is None and anchor_note is None


def test_anchor_check_on_accepts_a_resolvable_anchor(tmp_path) -> None:
    digest = _seeded_digest(tmp_path)
    dig = _digester(
        tmp_path,
        provider=_OneShotProvider(_json("trajectories/t1.md#step_0")),
        anchor_check=True,
    )
    returned, fb_note, anchor_note = asyncio.run(dig._interpret_task(None, digest, "ALL_FAIL"))
    assert returned.failure_category == "tool_effect_missing"  # kept
    assert fb_note is None and anchor_note is None


# ---------------------------------------------------------------------------
# flag / provenance
# ---------------------------------------------------------------------------


def test_anchor_check_flag_defaults_off() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).digest_anchor_check is False
    assert parser.parse_args(["--digest-anchor-check"]).digest_anchor_check is True


def test_anchor_check_provenance_none_when_off() -> None:
    assert rvp._digest_anchor_check_provenance(False) is None
    warn = rvp._digest_anchor_check_provenance(True)
    assert warn is not None
    assert "digest_anchor_check=on" in warn
    assert "byte-for-byte" in warn


def test_rationale_has_no_anchor_segment_when_none_rejected() -> None:
    # off (or on-but-clean): no anchor_check text -> bookkeeping line byte-identical.
    clean = rvp._LLMDigester._compose_rationale(
        core="c", n_passed=0, n_failed=1, interpreted=1, fallback_notes=[]
    )
    assert "anchor_check" not in clean
    # on and fired: a summary + per-digest reason appear.
    fired = rvp._LLMDigester._compose_rationale(
        core="c",
        n_passed=0,
        n_failed=1,
        interpreted=0,
        fallback_notes=[],
        anchor_rejections=["t1: anchor_check rejected digest (anchor target missing: x)"],
    )
    assert "[anchor_check: 1 digest(s) rejected]" in fired
    assert "anchor_check(disposition=rejected):" in fired
