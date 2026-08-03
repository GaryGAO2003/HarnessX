# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Run all offline novelty analyses A-E in order (zero API cost, read-only).

Usage:  python experiments/analysis/novelty/run_all.py [run]
        (E always runs on its own default cross-check set.)
"""

from __future__ import annotations

import sys

import a_selective_invocation as A
import b_allk_interception as B
import c_router_calibration as CC
import d_edit_type_gain as D
import e_cross_check_carriers as E
import _common as C


def main(run: str = C.DEFAULT_RUN) -> None:
    for mod in (A, B, CC, D):
        print("\n" + "=" * 78)
        mod.main(run)
    print("\n" + "=" * 78)
    E.main(E.DEFAULT_RUNS)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.DEFAULT_RUN)
