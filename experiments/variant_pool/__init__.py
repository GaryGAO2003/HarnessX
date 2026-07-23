# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline core of the HarnessX variant pool (SPEC stage A).

Pure-Python, dependency-light re-implementation of the variant-isolation
machinery of HarnessX §4.5. Nothing in this package imports ``run.py`` /
``agent.py`` or touches the network; see ``SPEC.md`` for the module contracts
and for which defaults are ours rather than the paper's.
"""

from __future__ import annotations

from .pool import DEFAULT_K, Variant, VariantPool

__all__ = [
    "DEFAULT_K",
    "Variant",
    "VariantPool",
]
