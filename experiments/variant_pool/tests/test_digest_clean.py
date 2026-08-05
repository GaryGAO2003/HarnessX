# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""batch-4a Item 2 -- P.1 Cleaner on the digester's windowed trajectory TEXT."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.evidence import EvidenceStore, TaskDigest  # noqa: E402


# ---------------------------------------------------------------------------
# _clean_window_text unit behaviour
# ---------------------------------------------------------------------------


def test_dedup_collapses_repeated_tool_output(tmp_path):
    text = (
        "### Step 0\n"
        "#### Tool Calls\n"
        "- **Fetch**(`{}`)\n"
        "  -> Fetch: REPEATED BODY LINE\n"
        "### Step 1\n"
        "#### Tool Calls\n"
        "- **Fetch**(`{}`)\n"
        "  -> Fetch: REPEATED BODY LINE\n"
    )
    out = rvp._clean_window_text(text, media_dir=tmp_path / "media")
    # first occurrence untouched, second collapsed to the dedup marker at step 0.
    assert out.count("REPEATED BODY LINE") == 1
    assert "[deduplicated: same output as step 0]" in out


def test_externalize_writes_media_file_and_replaces_inline(tmp_path):
    big = "X" * 3000  # > 2048 bytes
    text = (
        "### Step 0\n"
        "#### Tool Calls\n"
        "- **Bash**(`{}`)\n"
        f"  -> Bash: {big}\n"
    )
    media_dir = tmp_path / "media"
    out = rvp._clean_window_text(text, media_dir=media_dir)
    digest = hashlib.sha1(big.encode("utf-8")).hexdigest()[:16]
    assert big not in out  # inline body removed
    assert f"[externalized: media/{digest}.txt (3000 bytes)]" in out
    written = media_dir / f"{digest}.txt"
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == big


def test_below_threshold_not_externalized(tmp_path):
    small = "Y" * 100
    text = "### Step 0\n  -> Bash: " + small + "\n"
    out = rvp._clean_window_text(text, media_dir=tmp_path / "media")
    assert small in out
    assert "externalized" not in out
    assert not (tmp_path / "media").exists()  # no media dir created


def test_plain_text_round_trips_byte_for_byte(tmp_path):
    # No tool-result marker lines -> the cleaner must return the input verbatim.
    text = (
        "---\ntitle: t\n---\n\n"
        "## Execution Steps\n\n"
        "### Step 0\n#### Thinking\n\nsome reasoning\n#### Response\n\nan answer\n"
    )
    assert rvp._clean_window_text(text, media_dir=tmp_path / "media") == text


# ---------------------------------------------------------------------------
# _trajectory_window flag gating (byte-identical off)
# ---------------------------------------------------------------------------


class _FakeVariant:
    def __init__(self, routed):
        self.routed_tasks = set(routed)


class _FakePool:
    def __init__(self, variants):
        self.variants = dict(variants)


def _digester(run_dir: Path, **kw):
    evidence = EvidenceStore(run_dir)
    pool = _FakePool({"V0": _FakeVariant(())})
    return rvp._LLMDigester(
        evidence=evidence,
        pool=pool,
        provider=None,
        tasks_by_id={},
        run_dir=run_dir,
        fallback=rvp._EvidenceDigester(evidence, pool),
        **kw,
    )


_TRAJ_BODY = (
    "## Execution Steps\n\n"
    "### Step 0\n#### Tool Calls\n- **Fetch**(`{}`)\n  -> Fetch: SHARED OUTPUT TOKEN\n"
    "### Step 1\n#### Tool Calls\n- **Fetch**(`{}`)\n  -> Fetch: SHARED OUTPUT TOKEN\n"
)


def _write_traj(run_dir: Path) -> TaskDigest:
    traj = run_dir / "trajectories" / "t.md"
    traj.parent.mkdir(parents=True, exist_ok=True)
    traj.write_text("---\nk: v\n---\n\n" + _TRAJ_BODY, encoding="utf-8")
    return TaskDigest(
        task_id="t",
        round_idx=3,
        variant_id="V0",
        outcome=(0, 2),
        failure_category="x",
        evidence_anchors=["trajectories/t.md"],
    )


def test_digest_clean_defaults_off_and_window_byte_identical(tmp_path):
    digest = _write_traj(tmp_path)
    off = _digester(tmp_path)
    assert off.digest_clean is False
    _fm, head, tail = off._trajectory_window(digest)
    body = (head + tail)
    # off: no dedup marker; both SHARED OUTPUT TOKEN copies survive.
    assert body.count("SHARED OUTPUT TOKEN") == 2
    assert "deduplicated" not in body


def test_digest_clean_on_dedups_window(tmp_path):
    digest = _write_traj(tmp_path)
    on = _digester(tmp_path, digest_clean=True)
    _fm, head, tail = on._trajectory_window(digest)
    body = head + tail
    assert body.count("SHARED OUTPUT TOKEN") == 1
    assert "[deduplicated: same output as step 0]" in body
