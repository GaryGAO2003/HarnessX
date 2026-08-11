# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G1 test 5 — the vendored AEGIS package is content-unmodified.

'The official package is unmodified' is made an executable property, not a claim:
sha256 every file under harnessx/aegis/ and compare against a checked-in manifest.
A legitimate future re-vendor regenerates the manifest deliberately (see
``_regenerate`` below); an accidental edit to a vendored file fails this test.

Hashing is EOL-normalized (CRLF→LF before sha256), NOT raw-byte. Raw bytes were
tried first and produced a false alarm on 2026-08-11: the official pilot's own
snapshot/restore machinery rewrites some of its files in text mode at phase
boundaries (observed at L0 round-end, L1 startup, L2 critic phase — six files,
disk−blob size delta exactly equal to the line count, git content identical), so
on Windows every pilot run flips LF→CRLF and a raw-byte pin fails on every run
while the CONTENT is untouched. EOL churn is content-neutral for Python/Markdown;
any real edit still changes the normalized hash. The trade: an attack that ONLY
changes line endings is no longer caught — accepted, it is semantically inert.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harnessx import aegis

_AEGIS_ROOT = Path(aegis.__file__).parent
_MANIFEST = Path(__file__).parent / "vendored_aegis_manifest.json"


def _hash_tree() -> dict[str, str]:
    manifest: dict[str, str] = {}
    for p in sorted(_AEGIS_ROOT.rglob("*")):
        if p.is_dir():
            continue
        parts = p.relative_to(_AEGIS_ROOT).parts
        if "__pycache__" in parts or p.suffix == ".pyc":
            continue
        rel = p.relative_to(_AEGIS_ROOT).as_posix()
        manifest[rel] = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return manifest


def _regenerate() -> None:
    """Deliberate re-vendor helper: rewrite the manifest to match the tree.

    Not a test. Run manually (``python -c "from tests.ghx.test_vendored_integrity
    import _regenerate; _regenerate()"``) only when intentionally re-vendoring AEGIS.
    """
    _MANIFEST.write_text(json.dumps(_hash_tree(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_vendored_aegis_unmodified():
    expected = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    actual = _hash_tree()

    missing = sorted(set(expected) - set(actual))  # in manifest, gone from tree
    added = sorted(set(actual) - set(expected))  # new files not in manifest
    changed = sorted(f for f in (set(expected) & set(actual)) if expected[f] != actual[f])

    assert not missing, f"vendored files missing from tree: {missing}"
    assert not added, f"unexpected files under harnessx/aegis/: {added}"
    assert not changed, f"vendored files modified (content hash changed): {changed}"
    assert actual == expected
