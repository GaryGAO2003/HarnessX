# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Make ``variant_pool`` importable when these tests run from the repo root.

``experiments/`` is not a package, so put it on ``sys.path`` and import the
pool as the top-level package ``variant_pool``. Nothing else here: the tests
are fully offline and share no fixtures beyond pytest's ``tmp_path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EXPERIMENTS_DIR = Path(__file__).resolve().parents[2]
if str(_EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_EXPERIMENTS_DIR))
