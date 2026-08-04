# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Normalisation for artifact ``file:`` URIs (and bare paths) shared by loaders.

Agent-authored configs reference tools, processors, and templates by ``file://``
URI in several spellings, and the meta-agent also emits bare local paths
(``D:\\x\\y.py``). Every loader -- tool registry, processor builder, template
builder, direct-target parser -- routes through this single resolver so the same
spelling is repaired the same way instead of each loader re-deriving a naive
slice.
"""
from __future__ import annotations


def normalize_file_uri(target: str) -> str:
    """Resolve a ``file:`` URI (or a bare local path) to a local filesystem path.

    Handles every spelling the evolver and the recipe's round-tripping emit:

    * ``file:///D:/x.py`` / ``file:///D:\\x.py`` -- the RFC-style third slash the
      meta-agent writes. A naive ``target[len("file://"):]`` left ``/D:\\x`` in
      front of the drive, which Windows resolved against the current directory
      and raised.
    * ``file://D:/x.py`` -- the two-slash form ``to_yaml_file`` persists and a
      later round reads back. ``urllib.parse.urlparse`` mis-reads the drive
      letter as a netloc and drops it, so it cannot be used here.
    * ``D:/x.py`` / ``D:\\x.py`` -- a bare local path, returned untouched (no
      scheme, so a Windows drive letter is never mistaken for a URL scheme).
    * ``file:///home/u/x.py`` -- POSIX, resolved to ``/home/u/x.py`` on any host;
      extra leading slashes collapse to exactly one and it is never turned
      relative.

    Percent-escapes are decoded. Any ``::symbol`` suffix is left untouched for
    the caller to split. Normalisation only repairs URI spelling; it never
    rewrites or guesses a path that was not given.

    Mirrors ``recipe.gaia_evolver.run_variant_pool._as_local_path`` (the
    recipe-side twin), kept in step without importing across the layer.
    """
    if not target.startswith("file:"):
        return target
    from urllib.parse import unquote

    rest = unquote(target[len("file:") :])
    body = rest.lstrip("/")
    if len(body) >= 2 and body[0].isascii() and body[0].isalpha() and body[1] == ":":
        return body  # Windows absolute, whatever the slash count/separator
    return "/" + body if body else rest
