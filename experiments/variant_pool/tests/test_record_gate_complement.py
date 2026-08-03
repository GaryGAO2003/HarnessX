"""``--record-gate-complement`` (P5 -- the gate's non-carried measurements).

Why this exists. Deciding a fork evaluates the candidate on the parent's entire
``T_k``, but the child inherits only the tasks it improved, so only that slice
reaches its ledger. Across s1k8b103's 7 forks the gate measured 386 cells and 66
survived: 320 real measurements discarded, and the 66 kept are exactly the ones
the candidate did well on. The newborn's archive is therefore a sample selected
by the measurement being estimated, which is why 5 of 7 newborns entered their
next round optimistic (3 by more than +0.30) while taking the whole cluster in 7
of 7.

The engine already knows this: ``engine.py:591-595`` records the candidate's full
``T_k`` to the child and its comment names the failure mode outright ("recording
only improved tasks gives a child an optimistic prior"). It is off here because
``record_selected_results`` is ``candidate_mode != "paper"`` (``:4212``). Turning
that on wholesale would also change APPLY recording (``engine.py:569``) and would
re-record the carried slice, so the recipe writes only the complement.

The tests below pin the two things that make that safe: the complement excludes
what was already recorded, and default off writes nothing at all.
"""
import pytest

from recipe.gaia_evolver.run_variant_pool import (
    _record_gate_complement_provenance,
    build_arg_parser,
)


class _FakeLedger:
    def __init__(self):
        self.rows = []

    def record(self, variant_id, task_id, n_pass, n_att, round_idx):
        self.rows.append((variant_id, task_id, n_pass, n_att, round_idx))


class _FakeResult:
    def __init__(self, forked, per_variant_pass):
        self.forked = forked
        self.per_variant_pass = per_variant_pass


class _Runner:
    """Just enough surface to exercise the method unbound from the real runner."""

    from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
        VariantPoolRecipe as _Real,
    )

    _record_gate_complement = _Real._record_gate_complement

    def __init__(self, *, enabled, target, carried):
        self.record_gate_complement = enabled
        self._paper_target_variant = target
        self._active_round_pass = carried
        self.ledger = _FakeLedger()


GATE = {"t1": (2, 2), "t2": (0, 2), "t3": (1, 2), "t4": (0, 2)}
CARRIED = {"V1": {"t1": (2, 2), "t3": (1, 2)}}


def test_writes_only_the_complement():
    """t1/t3 are already in the ledger via the settled pass; only t2/t4 are new."""
    runner = _Runner(enabled=True, target="V0", carried=CARRIED)
    runner._record_gate_complement(_FakeResult(["V1"], {"V0": GATE}), 4)
    assert sorted(row[1] for row in runner.ledger.rows) == ["t2", "t4"]
    assert {row[0] for row in runner.ledger.rows} == {"V1"}


def test_complement_preserves_the_measured_outcome():
    runner = _Runner(enabled=True, target="V0", carried=CARRIED)
    runner._record_gate_complement(_FakeResult(["V1"], {"V0": GATE}), 4)
    assert dict((row[1], (row[2], row[3])) for row in runner.ledger.rows) == {
        "t2": (0, 2),
        "t4": (0, 2),
    }


def test_carried_tasks_are_never_re_recorded():
    """The double-count guard, stated directly rather than via the count."""
    runner = _Runner(enabled=True, target="V0", carried=CARRIED)
    runner._record_gate_complement(_FakeResult(["V1"], {"V0": GATE}), 4)
    written = {row[1] for row in runner.ledger.rows}
    assert written.isdisjoint(CARRIED["V1"])


def test_default_off_writes_nothing():
    runner = _Runner(enabled=False, target="V0", carried=CARRIED)
    runner._record_gate_complement(_FakeResult(["V1"], {"V0": GATE}), 4)
    assert runner.ledger.rows == []


@pytest.mark.parametrize(
    "forked, target", [([], "V0"), (["V1"], None)]
)
def test_no_fork_or_no_target_is_a_no_op(forked, target):
    runner = _Runner(enabled=True, target=target, carried=CARRIED)
    runner._record_gate_complement(_FakeResult(forked, {"V0": GATE}), 4)
    assert runner.ledger.rows == []


def test_child_carrying_the_whole_gate_set_yields_nothing():
    """Degenerate but real: an APPLY-shaped fork leaves an empty complement."""
    runner = _Runner(enabled=True, target="V0", carried={"V1": dict(GATE)})
    runner._record_gate_complement(_FakeResult(["V1"], {"V0": GATE}), 4)
    assert runner.ledger.rows == []


def test_provenance_is_none_when_off():
    """Load-bearing: a ``None`` here is what keeps the lock byte-identical."""
    assert _record_gate_complement_provenance(False) is None


def test_provenance_records_ours_attribution_when_on():
    text = _record_gate_complement_provenance(True)
    assert text is not None
    assert "OURS" in text
    assert "M-07" in text
    assert "not comparable byte-for-byte" in text


def test_flag_defaults_off():
    assert build_arg_parser().parse_args([]).record_gate_complement is False


def test_flag_sets_true():
    assert build_arg_parser().parse_args(["--record-gate-complement"]).record_gate_complement is True
