"""Stdlib-only fallback test runner (used when pytest is unavailable).

Discovers test_*.py modules in this directory, runs their test_* functions, and
injects a fresh temp directory for any function that declares a `tmp_path`
parameter (mirroring pytest's fixture). Run either as:

    python -m tests.run_all          # from the audit_arms directory
    python tests/run_all.py
"""

import importlib
import inspect
import os
import sys
import tempfile
import traceback
from pathlib import Path

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_AUDIT_ARMS = os.path.dirname(_TESTS_DIR)
for _p in (_AUDIT_ARMS, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    modules = sorted(f[:-3] for f in os.listdir(_TESTS_DIR)
                     if f.startswith("test_") and f.endswith(".py"))
    passed = failed = 0
    failures = []
    for modname in modules:
        mod = importlib.import_module(modname)
        for name, fn in sorted(inspect.getmembers(mod, inspect.isfunction)):
            if not name.startswith("test_") or fn.__module__ != modname:
                continue
            kwargs = {}
            if "tmp_path" in inspect.signature(fn).parameters:
                kwargs["tmp_path"] = Path(tempfile.mkdtemp(prefix="audit_arms_"))
            try:
                fn(**kwargs)
                passed += 1
            except Exception:  # noqa: BLE001
                failed += 1
                failures.append(f"{modname}::{name}\n{traceback.format_exc()}")
    print(f"\n{'=' * 60}")
    print(f"PASSED {passed}  FAILED {failed}")
    for f in failures:
        print("-" * 60)
        print(f)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
