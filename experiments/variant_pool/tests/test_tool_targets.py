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
    """End-to-end: the tool registers, both before and after the rewrite.

    The loader now normalises the RFC three-slash form itself, so the tool is
    present straight from the ``file:///`` config; the recipe-layer rewrite is
    defense-in-depth and must leave the loaded set unchanged.
    """
    rfc = _Registry(builtin=[], custom=[f"file:///{tool_file}::probe_tool"])

    before = set(_build_tool_registry_from_config(rfc).list_names())
    assert "probe_tool" in before, "the loader now normalises file:/// directly"

    fixed = _resolve_tool_targets(rfc, tool_file.parent / "config.yaml")
    after = set(_build_tool_registry_from_config(fixed).list_names())

    assert after == before, "the rewrite must not change what loads"


def test_the_rfc_spelling_now_parses_to_the_real_file(tool_file: Path):
    """The three-slash RFC form now resolves in the vendored loader.

    It used to leave a leading slash in front of the drive letter and break;
    the loader now normalises it (defense-in-depth), so ``_resolve_tool_targets``
    is belt-and-suspenders rather than the only thing that makes the tool arrive.
    """
    rfc = f"file:///{tool_file}::probe_tool"
    path_part, symbol = _parse_file_tool_target(rfc)

    assert symbol == "probe_tool"
    assert Path(path_part).is_file()
    assert Path(path_part) == tool_file


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


# ---------------------------------------------------------------------------
# 4. the vendored loader normalises every spelling the Evolver emits
#    (so an agent artefact loads regardless of slash count -- C-R8-04)
# ---------------------------------------------------------------------------
def _drive_uri(tool_file: Path, prefix: str, sep: str) -> str:
    """A ``file:`` URI for ``tool_file`` with the given slash count and sep."""
    return prefix + str(tool_file).replace("\\", sep) + "::probe_tool"


@pytest.mark.parametrize("prefix", ["file://", "file:///", "file:////"])
@pytest.mark.parametrize("sep", ["\\", "/"])
def test_every_drive_spelling_parses_to_the_same_file(tool_file: Path, prefix, sep):
    """Two-, three-, and four-slash drive URIs, both separators, all resolve.

    ``file:///`` (three slashes) is the RFC form the Evolver writes and the one
    that killed C-R8-04 by leaving ``/D:\\...`` in front of the drive letter; it
    must now land on the real file, as must the backslash and four-slash forms.
    """
    path_part, symbol = _parse_file_tool_target(_drive_uri(tool_file, prefix, sep))

    assert symbol == "probe_tool"
    assert Path(path_part).is_file()
    assert Path(path_part) == tool_file


def test_the_c_r8_04_killer_loads_through_the_registry(tool_file: Path):
    """The three-slash tool target that was silently dropped now registers.

    C-R8-04 declared ``file:///D:/...::pdf_fetch_tool``; the loader logged a
    warning and ran the candidate without the tool. The same shape must now
    register straight from the config, with no recipe-layer rewrite.
    """
    rfc = _Registry(builtin=[], custom=[f"file:///{tool_file}::probe_tool"])

    names = set(_build_tool_registry_from_config(rfc).list_names())
    assert "probe_tool" in names


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("file:///abs/path.py::probe_tool", "/abs/path.py"),
        ("file:////abs/path.py::probe_tool", "/abs/path.py"),
    ],
)
def test_posix_absolute_paths_keep_exactly_one_leading_slash(uri, expected):
    """A POSIX absolute path must never be collapsed into a relative one."""
    path_part, symbol = _parse_file_tool_target(uri)

    assert symbol == "probe_tool"
    assert path_part == expected


def test_normalisation_never_invents_a_missing_file(tmp_path: Path):
    """Spelling is repaired; a file that was never written stays absent.

    The path parses cleanly (the drive-letter slash is dropped) but points at
    nothing, so the loader registers no tool -- fail-closed, unchanged.
    """
    missing = tmp_path / "never_written.py"
    path_part, symbol = _parse_file_tool_target(f"file:///{missing}::probe_tool")
    assert symbol == "probe_tool"
    assert not Path(path_part).is_file()

    reg = _Registry(builtin=[], custom=[f"file:///{missing}::probe_tool"])
    assert "probe_tool" not in set(_build_tool_registry_from_config(reg).list_names())


def test_dotted_targets_never_enter_file_uri_normalisation():
    """A dotted target must go through module import, not file handling.

    A bogus dotted path fails as an import (no tool registered) rather than
    being mangled into a filesystem path, proving the file:// branch -- and its
    normalisation -- is skipped entirely for dotted targets.
    """
    reg = _Registry(builtin=[], custom=["nonexistent_pkg_zzz.some_tool_symbol"])
    assert set(_build_tool_registry_from_config(reg).list_names()) == set()
