# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Regressions for the two silent-failure path bugs found on 2026-08-02.

Both let a run keep going while doing the wrong thing, which is why they are
pinned here rather than left to integration:

1. ``run.py`` built a session directory name straight from the task id. A
   decomposition subtask's id is ``<parent>::<subtask>`` and ':' is illegal in a
   Windows path, so every subtask rollout died with WinError 123 before it
   reached the model.

2. The Evolver wrote ``file:///D:/...`` URIs into ``template_path``. Nothing
   validated them, the URI never opened, and the processor-crash handler
   swallowed the OSError -- so 634 active-pool rollouts (V5/V6/V7) ran on an
   *empty* system prompt while the run reported a healthy accuracy curve, plus
   a further 256 inside the candidate gate (V3/V4/V6), which means the
   ship/reject decisions were taken on the same corrupted signal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver.run import _fs_safe  # noqa: E402
from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
    _as_local_path,
    _resolve_artefact_paths,
)


# ---------------------------------------------------------------------------
# 1. session ids must survive being used as a directory name
# ---------------------------------------------------------------------------
def test_fs_safe_sanitises_the_subtask_separator():
    tid = "46719c30-f4c3-4cad-be07-d5cb21eee6bb::s4"
    out = _fs_safe(f"decomp-V4-{tid}")
    assert ":" not in out
    assert out == "decomp-V4-46719c30-f4c3-4cad-be07-d5cb21eee6bb__s4"


@pytest.mark.parametrize(
    "name",
    [
        "R3-V1-active-46719c30-f4c3-4cad-be07-d5cb21eee6bb",
        "R0-V0-active-00d579ea-0889-4fd9-a771-2c8d79835c8d-a2",
        "V0",
    ],
)
def test_fs_safe_is_a_noop_for_every_pre_existing_id(name):
    """Historical session directory names must not change."""
    assert _fs_safe(name) == name


# ---------------------------------------------------------------------------
# 2. artefact paths are normalised, then verified
# ---------------------------------------------------------------------------
def test_as_local_path_unwraps_a_file_uri(tmp_path: Path):
    target = tmp_path / "gaia_agent.j2"
    target.write_text("x", encoding="utf-8")
    assert _as_local_path(target.as_uri()) == target
    assert _as_local_path(str(target)) == target


def test_as_local_path_unwraps_the_malformed_uri_the_evolver_actually_writes(tmp_path: Path):
    """``file:///`` followed by *backslashes* -- not a well-formed file URI.

    ``Path.as_uri()`` produces the well-formed variant, so the test above was
    passing while never exercising the shape that occurs in practice. Observed
    live in s2k8b50 R1 (2026-08-02), from the same Evolver that caused M-27:

        file:///D:\\PycharmProj\\HarnessX\\...\\gaia_agent_commit_nudge.j2

    This matters because the validation is fail-closed: a form we cannot
    resolve no longer degrades quietly, it raises and takes the run down.
    """
    target = tmp_path / "gaia_agent_commit_nudge.j2"
    target.write_text("evolved", encoding="utf-8")

    malformed = "file:///" + str(target).replace("/", "\\")

    assert _as_local_path(malformed) == target
    assert _as_local_path(malformed).is_file()


def _proc(template_path: str) -> dict:
    return {
        "_target_": "harnessx.processors.context.system_prompt.SystemPromptProcessor",
        "system_builder": {
            "_target_": "...template.TemplateSystemPromptBuilder",
            "template_path": template_path,
        },
    }


def test_file_uri_is_rewritten_to_a_path_the_harness_can_open(tmp_path: Path):
    target = tmp_path / "gaia_agent_evolved.j2"
    target.write_text("evolved", encoding="utf-8")

    out = _resolve_artefact_paths([_proc(target.as_uri())], tmp_path / "config.yaml")

    got = out[0]["system_builder"]["template_path"]
    assert got == str(target)
    assert Path(got).is_file()


def test_unresolvable_artefact_fails_closed(tmp_path: Path):
    """The exact shape that produced 890 empty-prompt rollouts (634 + 256)."""
    missing = "file:///D:/PycharmProj/HarnessX/runs/x/templates/gaia_agent_evolved.j2"

    with pytest.raises(FileNotFoundError) as excinfo:
        _resolve_artefact_paths([_proc(missing)], tmp_path / "config.yaml")

    msg = str(excinfo.value)
    assert "template_path" in msg
    assert "empty system prompt" in msg


def test_processors_without_a_builder_pass_through_untouched(tmp_path: Path):
    others = [{"_target_": "some.other.Processor"}, {"_target_": "x", "system_builder": None}]
    assert _resolve_artefact_paths(list(others), tmp_path / "config.yaml") == others


def test_an_already_plain_path_is_left_byte_identical(tmp_path: Path):
    target = tmp_path / "gaia_agent.j2"
    target.write_text("base", encoding="utf-8")
    procs = [_proc(str(target))]

    out = _resolve_artefact_paths(procs, tmp_path / "config.yaml")

    assert out[0] is procs[0], "unchanged configs must not be copied"
