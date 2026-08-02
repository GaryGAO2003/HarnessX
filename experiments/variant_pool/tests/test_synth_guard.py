"""``--decomp-synth-guard`` (M-33): the synthesiser must not invent a missing result.

Observed on ``b_smoke`` task ``20194330``: the browse subtask returned nothing and
the synthesiser wrote "Based on the known content from the Game Grumps episode
...", i.e. it answered from parametric memory. A recalled answer that happens to
be right is scored as a pipeline success, so this failure mode inflates the
DECOMPOSED arm only -- it biases the headline contrast towards the thesis's own
hypothesis, which is the worst direction for a bias to run.

The guard is flag-gated and default-off; the first test is the one that matters
most, because a guard that silently altered the default prompt would invalidate
every arm run before it.
"""
import pytest

from experiments.variant_pool.subtask_pipeline import (
    SYNTH_GUARD_MODES,
    SYNTHESIS_GUARD_CLAUSE,
    SYNTHESIS_PROMPT_TEMPLATE,
    SubtaskSpec,
    Synthesizer,
)


def _outputs():
    return [
        (SubtaskSpec(id="s1", type="search", instruction="Find the episode", dep=[]), "found it"),
        (SubtaskSpec(id="s2", type="browse", instruction="Read the caption", dep=["s1"]), ""),
    ]


async def _never_called(prompt: str) -> str:  # pragma: no cover - guards misuse
    raise AssertionError("the completion seam must not be reached in prompt tests")


def test_default_is_byte_identical_to_the_stock_prompt():
    built = Synthesizer(_never_called).build_prompt("TASK", _outputs())
    expected = SYNTHESIS_PROMPT_TEMPLATE.format(
        task="TASK",
        subtasks="\n\n".join(
            f"### {spec.id} ({spec.type})\nInstruction: {spec.instruction}\nResult: {out}"
            for spec, out in _outputs()
        ),
    )
    assert built == expected
    assert SYNTHESIS_GUARD_CLAUSE not in built


def test_strict_inserts_the_clause_before_the_final_answer_contract():
    built = Synthesizer(_never_called, guard="strict").build_prompt("TASK", _outputs())
    assert SYNTHESIS_GUARD_CLAUSE in built
    # Order matters: the model must read the prohibition before the instruction
    # telling it to produce an answer, not after.
    assert built.index(SYNTHESIS_GUARD_CLAUSE) < built.index("FINAL ANSWER:")


def test_strict_preserves_the_final_answer_contract_the_gate_parses():
    built = Synthesizer(_never_called, guard="strict").build_prompt("TASK", _outputs())
    assert built.endswith("FINAL ANSWER: <answer>")


def test_strict_keeps_the_task_and_every_subtask_result():
    built = Synthesizer(_never_called, guard="strict").build_prompt("TASK", _outputs())
    assert "TASK" in built
    for spec, _ in _outputs():
        assert f"### {spec.id} ({spec.type})" in built
        assert spec.instruction in built


def test_the_clause_names_the_actual_failure_mode():
    # Wording is load-bearing: the observed fabrication was self-confident and
    # correct-sounding, so "even when it turns out to be correct" is the part
    # that has to survive future edits.
    assert "fabrication" in SYNTHESIS_GUARD_CLAUSE
    assert "even when it turns out to be correct" in SYNTHESIS_GUARD_CLAUSE
    assert "could not be obtained" in SYNTHESIS_GUARD_CLAUSE


def test_guard_fails_closed_if_the_template_stops_carrying_its_anchor(monkeypatch):
    # A reworded template must not turn the guard into a silent no-op: that would
    # leave the manifest claiming a protection the run never had -- the same
    # config-claims-what-the-runtime-drops shape this project keeps hitting.
    monkeypatch.setattr(
        "experiments.variant_pool.subtask_pipeline.SYNTHESIS_PROMPT_TEMPLATE",
        "ORIGINAL TASK:\n{task}\n\nRESULTS:\n{subtasks}\n\nAnswer now.",
    )
    with pytest.raises(RuntimeError, match="no longer contains"):
        Synthesizer(_never_called, guard="strict").build_prompt("TASK", _outputs())


def test_a_reworded_template_still_leaves_the_default_path_working(monkeypatch):
    monkeypatch.setattr(
        "experiments.variant_pool.subtask_pipeline.SYNTHESIS_PROMPT_TEMPLATE",
        "ORIGINAL TASK:\n{task}\n\nRESULTS:\n{subtasks}\n\nAnswer now.",
    )
    assert Synthesizer(_never_called).build_prompt("TASK", _outputs()).endswith("Answer now.")


@pytest.mark.parametrize("bad", ["on", "strict ", "STRICT", "", None, 1])
def test_unknown_modes_are_rejected_at_construction(bad):
    with pytest.raises(ValueError, match="guard must be one of"):
        Synthesizer(_never_called, guard=bad)


def test_declared_modes_are_exactly_the_cli_choices():
    assert SYNTH_GUARD_MODES == ("off", "strict")


@pytest.mark.asyncio
async def test_synthesize_sends_the_built_prompt_through_the_seam():
    seen = {}

    async def complete(prompt: str) -> str:
        seen["prompt"] = prompt
        return "FINAL ANSWER: 42"

    syn = Synthesizer(complete, guard="strict")
    out = await syn.synthesize(task_text="TASK", subtask_outputs=_outputs())
    assert out == "FINAL ANSWER: 42"
    assert seen["prompt"] == syn.build_prompt("TASK", _outputs())
