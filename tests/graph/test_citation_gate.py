"""Δ9 block 24 — citation gate: uncited declarations never reach the validator.

Ordering safety (the assessment's hazard): the gate must NOT starve the
validator — every WKD entry is citable via the Δ17 bootstrap view, locked
by test before the gate is considered open.
"""

import pytest

from harnessx.graph.declaration import (
    ComponentDecl,
    DeclarationSource,
    WELL_KNOWN_DECLARATIONS,
    backfill_declarations,
    citation_gate,
    cited_well_known,
    is_cited,
)

CG = "harnessx.processors.control.cost_guard.CostGuardProcessor"


def _decl(source, *, citations=None, evidence="", confidence=0.5):
    return ComponentDecl(
        target="x.P", hooks=("task_start",), source=source,
        confidence=confidence, llm_evidence=evidence,
        citations=citations or {},
    )


# ── is_cited truth table ────────────────────────────────────────────────────


def test_code_introspection_with_citations_is_cited():
    d = _decl(DeclarationSource.CODE_INTROSPECTION,
              citations={"singleton_group": "x.py:10"}, confidence=1.0)
    assert is_cited(d)


def test_code_introspection_without_citations_is_not():
    d = _decl(DeclarationSource.CODE_INTROSPECTION, confidence=1.0)
    assert not is_cited(d)  # "code scan" claims without file:line don't count


def test_observation_verified_is_cited():
    assert is_cited(_decl(DeclarationSource.OBSERVATION_VERIFIED,
                          confidence=0.95))


def test_llm_draft_is_never_cited_even_with_evidence():
    d = _decl(DeclarationSource.LLM_DRAFT,
              evidence="the class name suggests it writes memory")
    assert not is_cited(d)  # rationale is not provenance — 污染扩散 prevention


def test_unknown_is_not_cited():
    assert not is_cited(_decl(DeclarationSource.UNKNOWN, confidence=0.0))


# ── partition ───────────────────────────────────────────────────────────────


def test_citation_gate_partitions_without_discarding():
    cited = _decl(DeclarationSource.OBSERVATION_VERIFIED, confidence=0.95)
    draft = _decl(DeclarationSource.LLM_DRAFT)
    eligible, retrieval = citation_gate({"a": cited, "b": draft})
    assert set(eligible) == {"a"}
    assert set(retrieval) == {"b"}          # kept for retrieval, not dropped


# ── ordering safety: the gate must not starve the validator ─────────────────


def test_every_wkd_entry_is_citable():
    table = cited_well_known()
    assert set(table) == set(WELL_KNOWN_DECLARATIONS)
    for target, decl in table.items():
        assert is_cited(decl), f"{target}: gate would starve the validator"


def test_cited_view_is_cached():
    assert cited_well_known() is cited_well_known()


def test_cited_view_matches_static_snapshot_values():
    static = WELL_KNOWN_DECLARATIONS[CG]
    cited = cited_well_known()[CG]
    assert cited.hooks == static.hooks
    assert cited.order == static.order
    assert cited.citations  # and it carries what the snapshot cannot


# ── backfill with the gate on ───────────────────────────────────────────────


def test_backfill_gated_wkd_target_resolves_with_citations():
    out = backfill_declarations([CG], require_citations=True)
    assert is_cited(out[CG])
    assert out[CG].citations


def test_backfill_gated_uncited_hint_resolves_to_unknown():
    hint = _decl(DeclarationSource.LLM_DRAFT, evidence="sounds plausible")
    out = backfill_declarations(["x.P"], hints={"x.P": hint},
                                require_citations=True)
    assert out["x.P"].source is DeclarationSource.UNKNOWN  # validator sees nothing


def test_backfill_legacy_mode_unchanged():
    hint = _decl(DeclarationSource.LLM_DRAFT)
    out = backfill_declarations(["x.P"], hints={"x.P": hint})
    assert out["x.P"] is hint               # retrieval behaviour preserved


def test_backfill_gated_observation_hint_passes():
    hint = _decl(DeclarationSource.OBSERVATION_VERIFIED, confidence=0.95)
    out = backfill_declarations(["x.P"], hints={"x.P": hint},
                                require_citations=True)
    assert out["x.P"] is hint


# ── gate wiring ─────────────────────────────────────────────────────────────


def test_gate_quarantines_uncited_hints(tmp_path):
    from experiments.variant_pool.graph_gate import validate_candidate_graph

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "processors:\n"
        "  - _target_: harnessx.processors.control.cost_guard.CostGuardProcessor\n"
        "    _hook_: before_model\n",
        encoding="utf-8",
    )
    draft = _decl(DeclarationSource.LLM_DRAFT, evidence="guessed")
    report = validate_candidate_graph(cfg, declarations={"x.P": draft})
    assert report.passed                     # quarantine, not rejection
    assert any(w.error_type == "declaration_uncited" for w in report.warnings)


def test_gate_cited_hints_produce_no_quarantine(tmp_path):
    from experiments.variant_pool.graph_gate import validate_candidate_graph

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "processors:\n"
        "  - _target_: harnessx.processors.control.cost_guard.CostGuardProcessor\n"
        "    _hook_: before_model\n",
        encoding="utf-8",
    )
    verified = _decl(DeclarationSource.OBSERVATION_VERIFIED, confidence=0.95)
    report = validate_candidate_graph(cfg, declarations={"x.P": verified})
    assert report.passed
    assert not any(w.error_type == "declaration_uncited"
                   for w in report.warnings)
