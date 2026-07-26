# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""``MetaAgent`` + our structured candidate contract, without touching upstream.

The variant-pool experiment needs to inject a per-candidate *structured
candidate contract* into the meta-agent's ``TASK.md`` (the ``suggested_candidate_id``
/ ``target_variant`` the caller assigns to an isolated candidate slot, plus the
planner brief). The hard project constraint is that ``harnessx/`` stays a pure,
unmodified upstream — the variant pool is additive only — so we may **not** add
a ``candidate_contract`` parameter to :meth:`MetaAgent.evolve`.

Instead this recipe-layer subclass carries the contract *on the instance* and
overrides only the brief renderer to append the contract section to the base
brief. Because :meth:`MetaAgent._prepare_brief_and_context` and
:meth:`MetaAgent._render_task_brief` are ordinary instance methods, the override
composes cleanly and reuses ``super()``'s output verbatim.

Equivalence guarantees
----------------------
* With ``candidate_contract=None`` (the default), :meth:`_render_task_brief`
  returns ``super()``'s string unchanged, so the agent is byte-for-byte a native
  :class:`MetaAgent`.
* In ``paper`` manifest mode the appended section reproduces the exact wording of
  the (now-reverted) upstream ``_render_candidate_contract`` prototype, so the
  paper-fidelity ``TASK.md`` text is unchanged.
* In ``repo`` manifest mode (the default) the section deliberately drops the
  "write ``manifest.yaml``" requirement — see :mod:`recipe.gaia_evolver.run_variant_pool`
  (``--manifest-mode``): the caller adapts its machine-readable manifest from the
  meta-agent's repo-native products (the ``config.yaml`` diff and its journal
  ``levers`` / ``predicted_affected``), so the agent only has to write one
  artefact, not two.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from harnessx.meta_harness import MetaAgent


class VariantPoolMetaAgent(MetaAgent):
    """A :class:`MetaAgent` that appends our candidate contract to ``TASK.md``.

    The contract is per-candidate (each slot has a different
    ``suggested_candidate_id`` / ``target_variant`` and, on a retry, different
    decision feedback), and :meth:`MetaAgent.evolve`'s signature is frozen, so it
    is supplied through the constructor or — more usually — via
    :meth:`set_candidate_contract` immediately before each ``evolve`` call.
    """

    def __init__(
        self,
        *args: Any,
        candidate_contract: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._candidate_contract: Mapping[str, Any] | None = candidate_contract

    # ------------------------------------------------------------------
    # per-call contract
    # ------------------------------------------------------------------

    def set_candidate_contract(self, candidate_contract: Mapping[str, Any] | None) -> None:
        """Set the contract to inject on the *next* ``evolve`` call.

        ``evolve`` is upstream and takes no per-call contract argument, yet each
        candidate slot (and each retry, which carries the prior
        ``DECISION_REQUIRED`` feedback) needs a different one. The recipe calls
        this right before every ``evolve``; passing ``None`` restores native
        ``MetaAgent`` behaviour for that call.
        """
        self._candidate_contract = candidate_contract

    # ------------------------------------------------------------------
    # brief rendering — reuse super()'s output, append the contract
    # ------------------------------------------------------------------

    def _render_task_brief(self, **kwargs: Any) -> str:
        """Render the base brief, then append our contract section (if any).

        ``**kwargs`` forwards the upstream keyword-only signature
        (``current_config_path`` / ``trajectories_dir`` / ``output_dir`` /
        ``context_path``) untouched, so this stays correct even if upstream adds
        a brief field. The contract is read from the instance rather than a
        method argument precisely because upstream's call site does not (and must
        not) pass it.
        """
        base = super()._render_task_brief(**kwargs)
        section = self._render_candidate_contract(self._candidate_contract)
        return f"{base}{section}" if section else base

    @staticmethod
    def _render_candidate_contract(candidate_contract: Mapping[str, Any] | None) -> str:
        """Render the optional machine-readable candidate request into ``TASK.md``.

        The ``paper``-mode branch is verbatim the upstream prototype's wording
        (it requires ``_meta_scratch/manifest.yaml``). The ``repo``-mode branch
        (the default, read from ``planner_brief["manifest_mode"]``) keeps only the
        identity fields and explicitly tells the meta-agent it need not write a
        manifest — the caller adapts one from repo-native products.
        """
        if candidate_contract is None:
            return ""

        candidate_id = str(candidate_contract.get("suggested_candidate_id", "")).strip()
        target_variant = str(candidate_contract.get("target_variant", "")).strip()
        planner_brief = candidate_contract.get("planner_brief", {}) or {}
        manifest_mode = str(planner_brief.get("manifest_mode", "repo")).strip().lower()
        contract_json = json.dumps(
            {
                "suggested_candidate_id": candidate_id,
                "target_variant": target_variant,
                "planner_brief": planner_brief,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        identity_block = (
            f"- `suggested_candidate_id`: `{candidate_id}` (use exactly this value)\n"
            f"- `target_variant`: `{target_variant}` (use exactly this value)\n"
            f"- `planner_contract_json`: `{contract_json}`\n\n"
        )

        if manifest_mode == "paper":
            # Verbatim the reverted upstream prototype: manifest.yaml required.
            return (
                "\n## Structured candidate contract (caller-required)\n\n"
                "This evolve call is one isolated candidate slot. In addition to the "
                "normal deliverables, write `_meta_scratch/manifest.yaml` as a bare "
                "YAML mapping. The caller rejects the proposal if this file is "
                "missing, malformed, or incomplete; it will not infer fields from "
                "`candidates.md` or silently wrap `config.yaml`.\n\n"
                + identity_block
                + "Required manifest keys: `candidate_id`, `bucket`, `iterates_from`, "
                "`capability_evidence`, `file_changes`, `predicted_impact`, "
                "`attribution_signature`, and `target_variant`. Continue to write "
                "`_meta_scratch/candidates.md` when the config changes; the manifest "
                "is an additional machine-readable contract, not a replacement.\n\n"
            )

        # repo mode (default): one artefact, not two — no manifest.yaml required.
        return (
            "\n## Structured candidate contract (caller-required)\n\n"
            "This evolve call is one isolated candidate slot. Produce only your "
            "normal deliverables — `output_dir/config.yaml` plus the "
            "`_meta_scratch/candidates.md` and journal entry the base brief already "
            "requires. You do NOT need to write `_meta_scratch/manifest.yaml`: the "
            "caller adapts its machine-readable manifest from those repo-native "
            "products (the config diff and your journal `levers` / "
            "`predicted_affected`).\n\n"
            + identity_block
        )
