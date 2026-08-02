# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Regressions for the evolved-*processor* delivery path (found 2026-08-02).

Third member of the same family as ``test_artefact_paths.py`` (prompts) and
``test_tool_targets.py`` (tools). One test file per delivery path is deliberate:
the lesson from this batch is that fixing one path says nothing about the
others, and a shared file makes it too easy to believe otherwise.

This path is the worst of the three to detect, because it leaves no trace at
all. A prompt that will not open logs a processor crash; a tool that will not
load logs a warning; an evolved processor that will not build is swallowed by
``harnessx.core.harness._instantiate_proc``::

    def _instantiate_proc(d):
        try:
            return _instantiate(d)
        except Exception:
            return None          # no logging whatsoever

So the variant runs the stock processor stack while its config claims an
evolved one. Measured in s1k8b103: 19 active-pool configs affected, with the
instantiated set byte-identical to the stock baseline, and **zero** matching
lines across 408,880 log lines.

Two distinct causes, which is why the check is "does it instantiate" rather
than "does the file exist":

1. The path does not resolve -- same Windows ``file:///`` defect as the tools,
   since ``builder._parse_file_target`` strips the scheme the same naive way.
2. The config passes kwargs the referenced class version does not accept.
   s1k8b103 R10/V4 names ``CommitNudgeProcessor`` from an older candidate while
   passing ``nudge=``, which that version's ``__init__`` does not take. The file
   opens perfectly; only instantiation fails. An existence check misses this
   entirely -- it did, in the first version of this fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harnessx.core.builder import _instantiate  # noqa: E402
from recipe.gaia_evolver.run_variant_pool import _resolve_artefact_paths  # noqa: E402

#: A processor that takes exactly one keyword, so "wrong kwargs" is expressible.
PROCESSOR_SOURCE = '''
class NudgeProcessor:
    def __init__(self, warn_at_remaining: int = 3):
        self.warn_at_remaining = warn_at_remaining
'''


@pytest.fixture()
def processor_file(tmp_path: Path) -> Path:
    path = tmp_path / "commit_nudge.py"
    path.write_text(PROCESSOR_SOURCE, encoding="utf-8")
    return path


def _spec(target: str, **kwargs) -> dict:
    return {"_target_": target, **kwargs}


# ---------------------------------------------------------------------------
# 1. the rewrite, verified against the vendored builder
# ---------------------------------------------------------------------------
def test_rfc_spelling_is_the_one_that_breaks(processor_file: Path):
    """Pins why the emitted form is two-slash, so nobody 'corrects' it back."""
    with pytest.raises(Exception):
        _instantiate(_spec(f"file:///{processor_file}::NudgeProcessor"))


def test_rewritten_target_instantiates(processor_file: Path):
    out = _resolve_artefact_paths(
        [_spec(f"file:///{processor_file}::NudgeProcessor")], processor_file.parent / "c.yaml"
    )

    target = out[0]["_target_"]
    assert target.startswith("file://") and not target.startswith("file:///")
    assert type(_instantiate(dict(out[0]))).__name__ == "NudgeProcessor"


def test_kwargs_are_carried_through_the_rewrite(processor_file: Path):
    out = _resolve_artefact_paths(
        [_spec(f"file:///{processor_file}::NudgeProcessor", warn_at_remaining=7)],
        processor_file.parent / "c.yaml",
    )
    assert _instantiate(dict(out[0])).warn_at_remaining == 7


# ---------------------------------------------------------------------------
# 2. fail-closed on both causes
# ---------------------------------------------------------------------------
def test_missing_processor_file_fails_closed(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="silently"):
        _resolve_artefact_paths(
            [_spec(f"file:///{tmp_path / 'never_written.py'}::NudgeProcessor")],
            tmp_path / "c.yaml",
        )


def test_kwargs_the_class_does_not_accept_fail_closed(processor_file: Path):
    """The s1k8b103 R10/V4 shape: file opens, instantiation does not.

    This is the case an existence check cannot see, and the reason the gate is
    a real instantiation.
    """
    with pytest.raises(RuntimeError) as excinfo:
        _resolve_artefact_paths(
            [_spec(f"file:///{processor_file}::NudgeProcessor", nudge="commit now")],
            processor_file.parent / "c.yaml",
        )

    message = str(excinfo.value)
    assert "does not instantiate" in message
    assert "TypeError" in message
    assert "_instantiate_proc" in message, "the message should name what swallows it"


def test_target_without_a_symbol_is_rejected(processor_file: Path):
    with pytest.raises(ValueError, match="malformed"):
        _resolve_artefact_paths(
            [_spec(f"file:///{processor_file}")], processor_file.parent / "c.yaml"
        )


# ---------------------------------------------------------------------------
# 3. reach, and no collateral damage
# ---------------------------------------------------------------------------
def test_nested_targets_are_reached(processor_file: Path):
    """``builder._instantiate`` recurses into nested specs, so the fix must too."""
    nested = _spec(
        "harnessx.processors.context.system_prompt.SystemPromptProcessor",
        system_builder=_spec(f"file:///{processor_file}::NudgeProcessor"),
    )

    out = _resolve_artefact_paths([nested], processor_file.parent / "c.yaml")

    inner = out[0]["system_builder"]["_target_"]
    assert inner.startswith("file://") and not inner.startswith("file:///")


def test_dotted_targets_are_untouched_and_never_instantiated(tmp_path: Path):
    """Stock processors must not pay the cost, nor risk an import side effect."""
    dotted = _spec("harnessx.processors.context.system_prompt.SystemPromptProcessor", x=1)
    procs = [dotted]

    out = _resolve_artefact_paths(procs, tmp_path / "c.yaml")

    assert out[0] is dotted, "an unchanged spec must be returned by identity"


def test_a_plain_local_target_is_left_alone(processor_file: Path):
    """Already-normalised configs are re-read every round; they must not churn."""
    plain = _spec(f"file://{processor_file}::NudgeProcessor")
    out = _resolve_artefact_paths([plain], processor_file.parent / "c.yaml")

    assert out[0]["_target_"] == plain["_target_"]


def test_rewriting_is_idempotent(processor_file: Path):
    once = _resolve_artefact_paths(
        [_spec(f"file:///{processor_file}::NudgeProcessor")], processor_file.parent / "c.yaml"
    )
    twice = _resolve_artefact_paths(once, processor_file.parent / "c.yaml")

    assert twice[0]["_target_"] == once[0]["_target_"]
