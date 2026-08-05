# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Read-scope gate processor for the meta-agent.

Blocks Read / Grep / Glob / Bash calls that target restricted root
directories, with explicit per-file and per-subtree exceptions.  Intended to
prevent the meta-agent from spending steps deep-diving into harnessx source
code when the SKILL.md files already provide the necessary API surface, AND to
prevent cross-experiment leakage (e.g. reading archived prior runs' data into a
fresh run's decision-making context).

Windows-adapted port
--------------------
The upstream AEGIS gate extracts absolute paths from Bash commands with a
POSIX-only regex (``(?<![A-Za-z0-9_])(/[A-Za-z0-9_][A-Za-z0-9_./\\-]*)``).  On
Windows that regex is a silent no-op: ``D:\\x`` contains no leading-``/`` token
so it matches nothing, and ``D:/x`` matches the drive-less ``/x`` tail — the
wrong path, which then resolves against the current directory instead of the
drive.  This port extracts Windows drive-letter paths (``[A-Za-z]:[\\/]...``),
UNC paths (``\\\\server\\share\\...``), ``file://`` URIs (normalized through
the recipe's own :func:`harnessx.core.builder._resolve_target_path`, so every
``file:`` spelling the Evolver emits lands on the same local path), and POSIX
absolute paths.  Path comparison is case-insensitive on Windows
(``os.path.normcase`` casefolds and unifies separators on both sides).
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

from ...core.events import ToolCallEvent
from ...core.processor import MultiHookProcessor


def _resolve(value: str) -> Path | None:
    raw = (value or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        return None
    try:
        return p.resolve()
    except Exception:
        return None


def _norm(path: Path) -> str:
    """Case/separator-normalized string form for comparison.

    ``os.path.normcase`` casefolds and rewrites ``/`` to ``\\`` on Windows (so
    ``D:/Runs/x`` and ``d:\\runs\\X`` compare equal) and is the identity on
    POSIX (so case-sensitive filesystems stay case-sensitive).
    """
    return os.path.normcase(str(path))


def _is_within(child: Path, root: Path) -> bool:
    """True if ``child`` equals ``root`` or lives under it (case-insensitive on
    Windows), without ``Path.relative_to`` (which is case-sensitive)."""
    c = _norm(child)
    r = _norm(root)
    if c == r:
        return True
    if not r.endswith(os.sep):
        r = r + os.sep
    return c.startswith(r)


# Absolute path tokens inside a Bash command string. Meta-agents are trusted,
# not adversarial, so a plain regex is enough — we don't unpack shell
# expansions, subshells, quoted paths containing spaces, or obfuscated path
# construction. The goal is "catch the common case where the agent types /
# greps an archive directory directly", not "defeat a determined bypass".
# Ordered so the longest/most-specific spelling wins at each position:
# ``file://D:/x`` is consumed whole by the ``file:`` alternative before the
# drive or POSIX alternatives can pick off a fragment of it, and ``D:/x`` is
# consumed by the drive alternative before the POSIX alternative can grab the
# drive-less ``/x`` tail (the upstream bug).
_PATH_TOKEN_RE = re.compile(
    r"""(?x)
    file:[^\s"'|;&<>]+                                  # file: URI, any slash count
    | \\\\[^\s"'|;&<>]+                                 # UNC  \\server\share\...
    | (?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s"'|;&<>]*      # Windows drive  C:\... / C:/...
    | (?<![A-Za-z0-9_])/[A-Za-z0-9_][A-Za-z0-9_./\-]*   # POSIX absolute  /...
    """
)


def _extract_command_paths(command: str) -> list[str]:
    """Every absolute path-like token in a Bash command string.

    ``file://`` tokens are normalized through
    :func:`harnessx.core.builder._resolve_target_path` (imported lazily to keep
    this module cheap to import) so the RFC third-slash, two-slash and bare
    Windows-drive spellings all collapse to one local path. UNC tokens are
    extracted and compared like any other path; because the gate's blocked
    roots are local filesystem paths a UNC target never resolves under them and
    is therefore allowed — a documented limitation, not a bypass of a local
    block.
    """
    out: list[str] = []
    for match in _PATH_TOKEN_RE.finditer(command):
        token = match.group(0)
        if token.startswith("file:"):
            from ...core.builder import _resolve_target_path

            out.append(_resolve_target_path(token))
        else:
            out.append(token)
    return out


class ReadScopeGateProcessor(MultiHookProcessor):
    """Block tool calls that target restricted root directories.

    Paths listed in ``allowed_files`` (exact match) or under any
    ``allowed_roots`` subtree are always permitted even if they fall under a
    ``blocked_roots`` entry.  All other paths under ``blocked_roots`` are
    rejected with a helpful error message pointing the agent at the relevant
    SKILL.md instead.  ``allowed_roots`` is what lets the gate block the shared
    ``runs/`` archive while still admitting the meta-agent's own current run
    directory (its trajectories/round dirs), which lives inside that archive.

    Coverage:

    - ``Read``: checks ``file_path`` argument.
    - ``Grep`` / ``Glob``: checks ``path`` argument.
    - ``Bash``: extracts every absolute path token (Windows drive / UNC /
      ``file://`` / POSIX) from the ``command`` string and checks each.
      Substring-level check — does not expand ``$VAR``, subshells, tilde, or
      quoted paths containing spaces. Adequate for trusted-agent scoping (keep
      the meta-agent out of archived runs' directories), not adversarial
      sandboxing.
    """

    _singleton_group = "meta_read_scope_gate"
    _order = 4  # before write-scope gate (order 5)

    def __init__(
        self,
        blocked_roots: "tuple[str, ...] | None" = None,
        allowed_files: "tuple[str, ...] | None" = None,
        hint_message: str = "",
        allowed_roots: "tuple[str, ...] | None" = None,
    ) -> None:
        self._blocked_roots: tuple[Path, ...] = tuple(Path(x).resolve() for x in (blocked_roots or ()) if x)
        self._allowed_files: tuple[Path, ...] = tuple(Path(x).resolve() for x in (allowed_files or ()) if x)
        self._allowed_roots: tuple[Path, ...] = tuple(Path(x).resolve() for x in (allowed_roots or ()) if x)
        self._hint = hint_message

    def _is_blocked(self, path: Path) -> bool:
        resolved = path.resolve()
        # Exact-file and subtree allowlists win over any blocked root — this is
        # how the current run dir stays readable while its parent archive is
        # blocked. Case-insensitive on Windows (see :func:`_norm`).
        for exc in self._allowed_files:
            if _norm(resolved) == _norm(exc):
                return False
        for root in self._allowed_roots:
            if _is_within(resolved, root):
                return False
        for root in self._blocked_roots:
            if _is_within(resolved, root):
                return True
        return False

    def _blocked_msg(self, path: str) -> str:
        allowed = ", ".join(str(p) for p in self._allowed_files) or "(none)"
        msg = f"Read-scope gate: access to `{path}` is restricted.\n"
        if self._hint:
            msg += f"{self._hint}\n"
        msg += f"Allowed exceptions: {allowed}"
        return msg

    async def on_before_tool(self, event: ToolCallEvent):
        tool = event.tool_name
        if tool not in {"Read", "Grep", "Glob", "Bash"}:
            yield event
            return

        tool_input = event.tool_input or {}

        if tool == "Read":
            fp = tool_input.get("file_path", "")
            if isinstance(fp, str):
                p = _resolve(fp)
                if p is not None and self._is_blocked(p):
                    yield dataclasses.replace(
                        event,
                        approved=False,
                        synthetic_result=self._blocked_msg(fp),
                    )
                    return

        elif tool in {"Grep", "Glob"}:
            path_val = tool_input.get("path", "")
            if isinstance(path_val, str) and path_val.strip():
                p = _resolve(path_val)
                if p is not None and self._is_blocked(p):
                    yield dataclasses.replace(
                        event,
                        approved=False,
                        synthetic_result=self._blocked_msg(path_val),
                    )
                    return

        elif tool == "Bash":
            cmd = tool_input.get("command", "")
            if isinstance(cmd, str) and cmd:
                # Reject if any absolute path token resolves under a blocked
                # root. Trusted-agent scoping — does not defeat obfuscation.
                for candidate in _extract_command_paths(cmd):
                    p = _resolve(candidate)
                    if p is not None and self._is_blocked(p):
                        yield dataclasses.replace(
                            event,
                            approved=False,
                            synthetic_result=self._blocked_msg(candidate),
                        )
                        return

        yield event
