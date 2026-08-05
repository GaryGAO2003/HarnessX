"""Test config: make audit_arms modules + tests/helpers importable by bare name.

Uses sys.path self-insertion (the repo analysis-script idiom) rather than any
package machinery, so tests run identically under pytest or the plain fallback
runner. Zero dependence on the real runs directory -- all fixtures are synthetic.
"""

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_AUDIT_ARMS = _TESTS_DIR.parent
for _p in (str(_AUDIT_ARMS), str(_TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
