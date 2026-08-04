# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Normalisation for ``file://`` artifact URIs shared by the loaders.

Agent-authored configs reference tools, processors, and templates by
``file://`` URI, and several loaders (tool registry, processor builder,
template builder) strip that scheme before opening the path. Keeping the
scheme-stripping and path normalisation here means every loader repairs the
same spellings the same way instead of each re-deriving a naive slice.
"""
from __future__ import annotations

import re

# Canonical POSIX file URIs carry three slashes (``file:///D:/x``), so once the
# ``file://`` scheme is removed a Windows drive path arrives with one or more
# leading slashes in front of the drive letter. The OS cannot path-join such a
# string (``/D:/x`` joins to ``<cwd-drive>:\D:\x`` and raises ``[Errno 22]``),
# so a Windows drive path must not keep the leading slash.
_DRIVE_LEADING_SLASHES = re.compile(r"^[/\\]+(?=[A-Za-z]:[/\\])")


def normalize_file_uri(target: str) -> str:
    """Strip the ``file://`` scheme and normalise the path for the local OS.

    ``target`` is a ``file://`` URI with any leading-slash count (``file://``,
    ``file:///``, ``file:////``...). The scheme is removed and:

    * a Windows drive path loses every leading slash before the drive letter
      (``file:///D:/x`` -> ``D:/x``);
    * a POSIX absolute path keeps exactly one leading slash, collapsing any
      extras (``file:////abs/x`` -> ``/abs/x``); it is never turned relative.

    Backslashes in the remainder are tolerated. Any ``::symbol`` suffix is left
    untouched for the caller to split. Normalisation only repairs URI spelling;
    it never rewrites or guesses a path that was not given.
    """
    remainder = target[len("file://") :]
    drive = _DRIVE_LEADING_SLASHES.match(remainder)
    if drive:
        return remainder[drive.end() :]
    if remainder.startswith("/"):
        # POSIX absolute path: keep exactly one leading slash, never relative.
        return "/" + remainder.lstrip("/")
    return remainder
