# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Regressions for the evolved-tool delivery path (found 2026-08-02).

Same failure family as ``test_artefact_paths.py`` -- an evolved artefact that
is produced, referenced by the config, and then never loaded -- but on the
``tool_registry.custom`` path, which the M-27 fix did not cover.

What went wrong: the Evolver writes evolved tools as ``file://`` targets, and
``harnessx.core.harness`` parses them with a bare ``target[len("file://"):]``.
On Windows that leaves the leading slash of an RFC-style URI in front of the
drive letter, so ``file:///D:\\x`` becomes ``/D:\\x``, which resolves against
the current drive as ``D:\\D:\\x`` and raises ``[Errno 22]``. The loader logs
that at WARNING and carries on, so the variant runs without the tool.

Measured before the fix: 466 failures in s1k8b103 -- including the R2 and R4
*active-pool* configs for V1, so the pool itself and not merely its candidates
-- 40 in s2k8b50, and 14 in b_smoke, that last being every one of V1's
fourteen sessions.

The load-path tests below deliberately drive the **vendored** parser and
loader rather than asserting on string shape. Asserting the shape would keep
passing if someone "corrected" our two-slash output back to a standards-
compliant ``file:///``, which is exactly the change that reintroduces the bug.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harnessx.core.harness import (  # noqa: E402
    _build_tool_registry_from_config,
    _parse_file_tool_target,
)
from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
    _as_local_path,
    _resolve_tool_targets,
)

#: Same shape as a real evolved tool (see C-R1-01's ``python_eval``): the
#: decorator is called with keywords and wraps an async function. A bare
#: ``@tool`` produces a plain function and the registry rejects it, which would
#: make these tests fail for a reason unrelated to path resolution.
TOOL_SOURCE = '''
from harnessx.tools.base import tool

_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


@tool(name="probe_tool", description="Echo, so the loader has something to import.",
      input_schema=_SCHEMA)
async def probe_tool(x: str) -> str:
    return x
'''


@dataclasses.dataclass
class _Registry:
    """Stands in for ToolRegistryConfig: same two fields, same dataclass-ness."""

    builtin: list
    custom: list


@pytest.fixture()
def tool_file(tmp_path: Path) -> Path:
    path = tmp_path / "probe_tool.py"
    path.write_text(TOOL_SOURCE, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. every spelling of a file URI that occurs in real configs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("prefix", ["file:///", "file://"])
@pytest.mark.parametrize("sep", ["\\", "/"])
def test_as_local_path_accepts_every_windows_spelling(tmp_path: Path, prefix, sep):
    """Both slash counts and both separators must land on the same file.

    ``file:///`` is what the Evolver writes; ``file://`` is what
    ``_resolve_tool_targets`` emits and ``to_yaml_file`` then persists, so a
    later round reads our own output back and must still resolve it.
    """
    target = tmp_path / "probe_tool.py"
    target.write_text("x", encoding="utf-8")
    spelling = prefix + str(target).replace("\\", sep)

    assert _as_local_path(spelling) == target
    assert _as_local_path(spelling).is_file()


def test_as_local_path_leaves_a_plain_path_alone(tmp_path: Path):
    target = tmp_path / "probe_tool.py"
    target.write_text("x", encoding="utf-8")
    assert _as_local_path(str(target)) == target


# ---------------------------------------------------------------------------
# 2. the rewritten target must survive the *vendored* parser
# ---------------------------------------------------------------------------
def test_rewritten_target_loads_through_the_vendored_loader(tool_file: Path):
    """End-to-end: the tool is absent before the rewrite and present after.

    This is the assertion that actually matters. ``harnessx/`` is vendored and
    not ours to patch, so the only thing that makes the tool arrive is the
    shape we hand it.
    """
    broken = _Registry(builtin=[], custom=[f"file:///{tool_file}::probe_tool"])

    before = set(_build_tool_registry_from_config(broken).list_names())
    assert "probe_tool" not in before, "fixture no longer reproduces the bug"

    fixed = _resolve_tool_targets(broken, tool_file.parent / "config.yaml")
    after = set(_build_tool_registry_from_config(fixed).list_names())

    assert "probe_tool" in after
    assert after - before == {"probe_tool"}, "the rewrite must not disturb other tools"


def test_the_rfc_spelling_is_the_one_that_breaks(tool_file: Path):
    """Pins *why* the emitted form is two-slash, so nobody 'corrects' it back.

    If a future change makes the RFC spelling work in the vendored loader this
    test fails loudly, which is the right moment to revisit the rewrite.
    """
    rfc = f"file:///{tool_file}::probe_tool"
    path_part, symbol = _parse_file_tool_target(rfc)

    assert symbol == "probe_tool"
    assert not Path(path_part).is_file(), (
        "the vendored parser has started handling file:/// correctly; "
        "_resolve_tool_targets can be simplified"
    )


# ---------------------------------------------------------------------------
# 3. fail-closed, and no collateral damage
# ---------------------------------------------------------------------------
def test_missing_tool_file_fails_closed(tmp_path: Path):
    """A tool the variant is defined by that cannot be opened is a broken variant."""
    missing = _Registry(
        builtin=[], custom=[f"file:///{tmp_path / 'never_written.py'}::probe_tool"]
    )

    with pytest.raises(FileNotFoundError) as excinfo:
        _resolve_tool_targets(missing, tmp_path / "config.yaml")

    message = str(excinfo.value)
    assert "incomplete tool set" in message
    assert "never_written.py" in message


def test_a_target_without_a_symbol_is_rejected(tmp_path: Path):
    target = tmp_path / "probe_tool.py"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed"):
        _resolve_tool_targets(
            _Registry(builtin=[], custom=[f"file:///{target}"]), tmp_path / "config.yaml"
        )


def test_dotted_module_targets_are_untouched(tmp_path: Path):
    """The serper tool is registered this way in every config; it must not move."""
    dotted = "harnessx.tools.contrib.serper_search.serper_web_search_tool"
    registry = _Registry(builtin=["Bash"], custom=[dotted])

    out = _resolve_tool_targets(registry, tmp_path / "config.yaml")

    assert out is registry, "a config needing no rewrite must not be copied"
    assert out.custom == [dotted]


def test_empty_and_absent_custom_lists_are_identity(tmp_path: Path):
    for registry in (_Registry(builtin=["Bash"], custom=[]), _Registry(builtin=[], custom=None)):
        assert _resolve_tool_targets(registry, tmp_path / "config.yaml") is registry


def test_mixed_list_keeps_order_and_rewrites_only_the_file_target(tool_file: Path):
    dotted = "harnessx.tools.contrib.serper_search.serper_web_search_tool"
    registry = _Registry(builtin=[], custom=[dotted, f"file:///{tool_file}::probe_tool"])

    out = _resolve_tool_targets(registry, tool_file.parent / "config.yaml")

    assert out.custom[0] == dotted
    assert out.custom[1].startswith("file://") and not out.custom[1].startswith("file:///")
    assert _as_local_path(out.custom[1].rpartition("::")[0]) == tool_file


def test_rewriting_is_idempotent(tool_file: Path):
    """Our own output is read back by later rounds via to_yaml_file."""
    registry = _Registry(builtin=[], custom=[f"file:///{tool_file}::probe_tool"])
    once = _resolve_tool_targets(registry, tool_file.parent / "config.yaml")
    twice = _resolve_tool_targets(once, tool_file.parent / "config.yaml")

    assert twice.custom == once.custom
    assert twice is once, "a second pass should be a no-op, not another copy"
