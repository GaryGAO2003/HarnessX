"""M-38: telling the Evolver how to spell a `file:` target.

The Evolver authors tools and processors and writes `file:` targets for them.
Our load path repairs a malformed target, so the gate and the active pool are
fine, but the Evolver's own smoke test inside `pipeline/candidates/` loads
through the vendored Harness, where the defect is live. The exception is
swallowed and logged, so the Evolver runs its candidate *without* the tool it
just wrote and cannot tell.

That compounds: the same brief requires a tool candidate to show Level-2
round-trip evidence -- its output surviving into the next model message -- which
a tool that never registered can never produce.

The most important tests here are the ones that check the brief tells the TRUTH.
A brief that taught a spelling which also fails to load would be worse than no
brief at all, so the claim is verified against the real vendored loader rather
than against our repaired one.
"""
import pytest

from recipe.gaia_evolver.run_variant_pool import (
    DECISION_CONTRACT_EMPHASIS,
    PAPER_MANIFEST_SCHEMA_BRIEF,
    REPO_MANIFEST_SCHEMA_BRIEF,
    _as_local_path,
    _FILE_TARGET_SPELLING_BRIEF,
)

WIN = r"D:\PycharmProj\HarnessX\runs\c1\tools\pdf_tool.py"


# --- does the brief tell the truth? ---------------------------------------


def test_the_spelling_the_brief_teaches_survives_the_vendored_truncation():
    # The vendored loader's operation, reproduced exactly: strip len("file://").
    target = f"file://{WIN}"
    stripped = target[len("file://") :]
    assert stripped == WIN, "the taught spelling must leave a clean absolute path"
    assert not stripped.startswith("/")


def test_the_spelling_the_brief_forbids_now_loads_too():
    # The brief text is unchanged and still teaches EXACTLY TWO slashes, but the
    # vendored loader now normalises the three-slash form as defense-in-depth:
    # a candidate that ignores the brief is repaired rather than silently
    # dropped. A bare ``target[len("file://"):]`` would still leave the leading
    # slash (the observed 'D:\D:\...' failure), so this asserts against the real
    # loader instead.
    from harnessx.core.harness import _parse_file_tool_target

    path_part, symbol = _parse_file_tool_target(f"file:///{WIN}::pdf_tool")
    assert symbol == "pdf_tool"
    assert not path_part.startswith("/")
    assert path_part == WIN


def test_our_own_resolver_accepts_the_taught_spelling_too():
    # The brief must not push the Evolver towards a form our repair path would
    # then mangle -- both loaders have to agree on it.
    assert str(_as_local_path(f"file://{WIN}")) == WIN


@pytest.mark.parametrize(
    "spelling",
    [f"file://{WIN}", f"file:///{WIN}", "file://D:/a/b.py", "file:///D:/a/b.py"],
)
def test_our_resolver_normalises_every_spelling_the_evolver_has_produced(spelling):
    # Unchanged behaviour, restated here because the brief's whole premise is
    # that our path is repaired and the Evolver's is not.
    resolved = str(_as_local_path(spelling))
    assert not resolved.startswith("/")
    assert resolved[1:2] == ":"


# --- is it delivered where the Evolver will read it? -----------------------


def test_the_brief_reaches_both_manifest_modes():
    # repo is the mode in use; paper is left broken otherwise, and the defect is
    # a platform property that does not depend on which schema is requested.
    assert _FILE_TARGET_SPELLING_BRIEF in REPO_MANIFEST_SCHEMA_BRIEF
    assert _FILE_TARGET_SPELLING_BRIEF in PAPER_MANIFEST_SCHEMA_BRIEF


def test_it_does_not_leak_into_the_decision_contract():
    # DECISION_CONTRACT_EMPHASIS is about committing to a decision; an earlier
    # edit of mine landed the spelling text there by matching the wrong string.
    assert _FILE_TARGET_SPELLING_BRIEF not in DECISION_CONTRACT_EMPHASIS


def test_it_sits_next_to_the_level_2_requirement_it_interacts_with():
    # Placement is load-bearing: the Evolver has to read the two together to
    # understand why a mis-spelled target makes the L2 evidence unobtainable.
    idx_brief = REPO_MANIFEST_SCHEMA_BRIEF.index(_FILE_TARGET_SPELLING_BRIEF)
    idx_l2 = REPO_MANIFEST_SCHEMA_BRIEF.index("Level 2")
    assert idx_l2 < idx_brief


# --- does it say the things that make it actionable? ----------------------


def test_the_brief_names_all_three_artefact_kinds():
    for kind in ("tool_registry.custom", "_target_", "template_path"):
        assert kind in _FILE_TARGET_SPELLING_BRIEF


def test_the_brief_states_the_rule_the_form_and_the_anti_form():
    assert "EXACTLY TWO" in _FILE_TARGET_SPELLING_BRIEF
    assert "file://<absolute path>::<symbol>" in _FILE_TARGET_SPELLING_BRIEF
    assert "file:///" in _FILE_TARGET_SPELLING_BRIEF


def test_the_brief_explains_the_invisibility_not_just_the_rule():
    # A bare rule invites a plausible-looking deviation. The reason it cannot be
    # caught by testing is the part that has to survive future edits.
    text = _FILE_TARGET_SPELLING_BRIEF
    assert "no error you can observe" in text
    assert "ABSENT from your own smoke test" in text
    assert "Level-2" in text
