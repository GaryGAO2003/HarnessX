# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--meta-read-scope-gate`` (batch-2b Item 1).

The upstream AEGIS read-scope gate extracts Bash paths with a POSIX-only regex
that silently blocks NOTHING on Windows (``D:\\x`` has no ``/`` token; ``D:/x``
matches the drive-less ``/x`` -- the wrong path). These tests assert the CURE:
the Windows-adapted port actually blocks a Bash ``type``/``cat`` of a file under
a blocked root in both the backslash (``D:\\...``) and forward-slash (``D:/...``)
spellings, the ``file://`` spelling, and Read/Grep/Glob -- while the meta-agent's
own current run dir (a subtree of the blocked ``runs/`` archive) stays readable.

The path construction uses ``tmp_path`` so the same test exercises real,
resolvable absolute paths on whatever platform runs it: on Windows those are
drive paths (the forms the upstream regex mishandles), on POSIX they are
``/tmp/...`` paths. Fully offline; no harness, no provider.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harnessx.core.events import ToolCallEvent  # noqa: E402
from harnessx.meta_harness.processors.read_scope_gate import (  # noqa: E402
    ReadScopeGateProcessor,
)
from recipe.gaia_evolver.run_variant_pool import _meta_read_scope_roots  # noqa: E402


def _drive(proc: ReadScopeGateProcessor, event: ToolCallEvent) -> list[ToolCallEvent]:
    async def _collect() -> list[ToolCallEvent]:
        return [e async for e in proc.on_before_tool(event)]

    return asyncio.run(_collect())


def _bash(command: str) -> ToolCallEvent:
    return ToolCallEvent(
        run_id="r", step_id=0, tool_name="Bash", tool_input={"command": command}
    )


def _gate(tmp_path: Path) -> tuple[ReadScopeGateProcessor, Path, Path]:
    """A gate blocking ``<tmp>/runs`` except the current run ``<tmp>/runs/current``."""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "current"
    archived = runs_root / "archived"
    (run_dir / "R1").mkdir(parents=True, exist_ok=True)
    archived.mkdir(parents=True, exist_ok=True)
    (archived / "x.md").write_text("secret prior run", encoding="utf-8")
    (run_dir / "R1" / "traj.md").write_text("my own trajectory", encoding="utf-8")
    proc = ReadScopeGateProcessor(
        blocked_roots=(str(runs_root),),
        allowed_roots=(str(run_dir),),
        hint_message="gated",
    )
    return proc, run_dir, archived


def _blocked(out: list[ToolCallEvent]) -> bool:
    return len(out) == 1 and out[0].approved is False and bool(out[0].synthetic_result)


def _allowed(out: list[ToolCallEvent]) -> bool:
    return len(out) == 1 and out[0].approved is True and out[0].synthetic_result is None


# ---------------------------------------------------------------------------
# 1. Bash backslash form (``D:\...\archived\x.md``) is blocked.
# ---------------------------------------------------------------------------
def test_bash_backslash_archived_is_blocked(tmp_path: Path) -> None:
    proc, _run_dir, archived = _gate(tmp_path)
    target = archived / "x.md"
    # str(Path) renders backslashes on Windows -- the form the upstream regex
    # cannot see at all.
    out = _drive(proc, _bash(f"type {target}"))
    assert _blocked(out)


# ---------------------------------------------------------------------------
# 2. Bash forward-slash form (``D:/...``) is blocked (upstream matched ``/...``).
# ---------------------------------------------------------------------------
def test_bash_forward_slash_archived_is_blocked(tmp_path: Path) -> None:
    proc, _run_dir, archived = _gate(tmp_path)
    target = archived / "x.md"
    out = _drive(proc, _bash(f"cat {target.as_posix()}"))
    assert _blocked(out)


# ---------------------------------------------------------------------------
# 3. Bash file:// spelling is blocked (normalized via _resolve_target_path).
# ---------------------------------------------------------------------------
def test_bash_file_uri_archived_is_blocked(tmp_path: Path) -> None:
    proc, _run_dir, archived = _gate(tmp_path)
    target = archived / "x.md"
    out = _drive(proc, _bash(f"cat file:///{target.as_posix()}"))
    assert _blocked(out)


# ---------------------------------------------------------------------------
# 4. The meta-agent's OWN current run dir stays readable (subtree allowlist),
#    even though it lives under the blocked runs/ archive.
# ---------------------------------------------------------------------------
def test_bash_current_run_dir_is_allowed(tmp_path: Path) -> None:
    proc, run_dir, _archived = _gate(tmp_path)
    own = run_dir / "R1" / "traj.md"
    out = _drive(proc, _bash(f"cat {own}"))
    assert _allowed(out)
    out_slash = _drive(proc, _bash(f"cat {own.as_posix()}"))
    assert _allowed(out_slash)


# ---------------------------------------------------------------------------
# 5. A benign Bash command touching nothing under a blocked root passes.
# ---------------------------------------------------------------------------
def test_bash_unrelated_command_passes(tmp_path: Path) -> None:
    proc, _run_dir, _archived = _gate(tmp_path)
    out = _drive(proc, _bash("echo hello && ls -la"))
    assert _allowed(out)


# ---------------------------------------------------------------------------
# 6. Read/Grep/Glob honor the same blocked-root / allowlist rules.
# ---------------------------------------------------------------------------
def test_read_grep_glob_blocked_and_allowed(tmp_path: Path) -> None:
    proc, run_dir, archived = _gate(tmp_path)

    read_blocked = ToolCallEvent(
        run_id="r", step_id=0, tool_name="Read",
        tool_input={"file_path": str(archived / "x.md")},
    )
    assert _blocked(_drive(proc, read_blocked))

    read_allowed = ToolCallEvent(
        run_id="r", step_id=0, tool_name="Read",
        tool_input={"file_path": str(run_dir / "R1" / "traj.md")},
    )
    assert _allowed(_drive(proc, read_allowed))

    grep_blocked = ToolCallEvent(
        run_id="r", step_id=0, tool_name="Grep",
        tool_input={"pattern": "x", "path": str(archived)},
    )
    assert _blocked(_drive(proc, grep_blocked))

    glob_allowed = ToolCallEvent(
        run_id="r", step_id=0, tool_name="Glob",
        tool_input={"pattern": "*.md", "path": str(run_dir)},
    )
    assert _allowed(_drive(proc, glob_allowed))


# ---------------------------------------------------------------------------
# 7. Case-insensitive comparison on Windows (a re-cased drive path still blocks).
# ---------------------------------------------------------------------------
def test_case_insensitive_block_on_windows(tmp_path: Path) -> None:
    if sys.platform != "win32":
        return  # POSIX filesystems are case-sensitive by design; nothing to prove
    proc, _run_dir, archived = _gate(tmp_path)
    recased = str(archived / "x.md").upper()
    out = _drive(proc, _bash(f"type {recased}"))
    assert _blocked(out)


# ---------------------------------------------------------------------------
# 8. Wiring helper: flag OFF returns None (caller wires nothing -> no processor
#    -> byte-identical); flag ON returns the three read-scope kwargs.
# ---------------------------------------------------------------------------
def test_meta_read_scope_roots_off_is_none(tmp_path: Path) -> None:
    args = argparse.Namespace(meta_read_scope_gate=False)
    assert _meta_read_scope_roots(args, tmp_path) is None


def test_meta_read_scope_roots_on_builds_kwargs(tmp_path: Path) -> None:
    args = argparse.Namespace(meta_read_scope_gate=True)
    kws = _meta_read_scope_roots(args, tmp_path)
    assert kws is not None
    assert set(kws) == {
        "read_scope_blocked_roots",
        "read_scope_allowed_files",
        "read_scope_allowed_roots",
    }
    # The current run dir is allowlisted as a subtree...
    assert kws["read_scope_allowed_roots"] == (str(tmp_path.resolve()),)
    # ...and the harnessx package dir + this recipe's runs/ archive are blocked.
    blocked = kws["read_scope_blocked_roots"]
    assert len(blocked) == 2
    assert any(b.endswith("harnessx") for b in blocked)
    assert any(b.replace("\\", "/").endswith("gaia_evolver/runs") for b in blocked)
    # harnessx/core/processor.py stays readable (the hint promises it).
    assert kws["read_scope_allowed_files"][0].replace("\\", "/").endswith(
        "harnessx/core/processor.py"
    )
