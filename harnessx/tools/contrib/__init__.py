# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Additive, opt-in tool contributions that ship *alongside* the built-in set.

Nothing in this package is imported or registered by default — the built-in
registries (``harnessx.tools.builtin``) are untouched. A contrib tool becomes
active only when a caller (e.g. a recipe) explicitly imports and wires it in.

This mirrors the additive convention already used for
``harnessx/processors/control/step_countdown.py``: a capability is added as a
*new file* under ``harnessx/`` without editing any existing ``harnessx/`` file.
"""
