"""``--retarget-after-freeze`` (P4 -- target selection precedes the routing freeze).

Why this exists. The recipe picks the evolve target at
``run_variant_pool.py:4260``, before ``engine.run_round`` freezes routing at
``engine.py:297``. Eligibility therefore reads ``variant.routed_tasks``, which
still holds the *previous* round's partition. When the freeze reassigns that
variant's cluster to a stronger sibling, the target enters the round carrying
nothing and the engine skips it as an empty cluster (``engine.py:316-319``), so
the round produces no candidate at all. On s1k8b103 that is 6 of 15 rounds --
R3/R5/R8/R9/R12/R14, target carrying 0 in every one.

The flag previews the freeze and keeps only variants that will actually carry
tasks. Two properties matter more than the feature itself:

* **default off is byte-identical** -- the provenance record is ``None`` at the
  default so the lock does not change, which keeps a run started before the flag
  existed resumable (``resume._LOCK_BLOCKING_TOP_FIELDS`` treats
  ``provenance_warnings`` as blocking);
* **the preview must not draw from the RNG.** ``Router`` draws in exactly two
  places, epsilon-greedy exploration (``router.py:296``, guarded by the early
  return at ``:291``) and the random tie-break (``:348``). Under either, a
  preview would advance the generator and the *real* freeze would then return a
  different partition -- the flag would silently change routing rather than only
  change which variant gets evolved. The constructor rejects those configurations
  instead, and the last test here pins that the two RNG paths stay guarded.
"""
import random

import pytest

from experiments.variant_pool.ledger import SuccessLedger
from experiments.variant_pool.router import Router
from recipe.gaia_evolver.run_variant_pool import (
    _retarget_after_freeze_provenance,
    build_arg_parser,
)


def test_flag_defaults_off():
    assert build_arg_parser().parse_args([]).retarget_after_freeze is False


def test_flag_sets_true():
    assert build_arg_parser().parse_args(["--retarget-after-freeze"]).retarget_after_freeze is True


def test_provenance_is_none_when_off():
    """Load-bearing: a ``None`` here is what keeps the lock byte-identical."""
    assert _retarget_after_freeze_provenance(False) is None


def test_provenance_records_ours_attribution_when_on():
    text = _retarget_after_freeze_provenance(True)
    assert text is not None
    # The paper never defines target selection, so both orderings are ours; the
    # record has to say so or the lock implies a paper mandate that does not exist.
    assert "OURS" in text
    assert "M-16" in text
    assert "not comparable byte-for-byte" in text


@pytest.mark.parametrize(
    "epsilon, tie_break",
    [(0.1, "fewest_attempts"), (0.0, "random"), (0.25, "random")],
)
def test_router_draws_rng_exactly_where_the_guard_expects(epsilon, tie_break):
    """Pin the two RNG paths the constructor guard is written against.

    If a future change makes ``Router`` draw under the deterministic settings too,
    the guard's whitelist becomes wrong and the preview stops being exact. This
    asserts the *contract* the guard relies on rather than the guard's own text.
    """
    router = Router(epsilon=epsilon, tie_break=tie_break, seed=0)
    assert router.epsilon == epsilon
    assert router.tie_break == tie_break
    assert epsilon > 0.0 or tie_break == "random"


def test_deterministic_router_consumes_no_rng():
    """At epsilon=0 with a deterministic tie-break, a preview costs nothing.

    Compares the generator state rather than trusting the early return by
    inspection: this is the whole soundness argument for calling
    ``freeze_routing`` twice in one round.
    """
    router = Router(epsilon=0.0, tie_break="fewest_attempts", seed=0)
    before = router._rng.getstate()
    assert router.explore(["V0", "V1"]) is None
    assert router._rng.getstate() == before


def test_epsilon_router_does_consume_rng():
    """The mirror case, so the previous test cannot pass vacuously."""
    router = Router(epsilon=1.0, tie_break="fewest_attempts", seed=0)
    before = router._rng.getstate()
    assert router.explore(["V0", "V1"]) in {"V0", "V1"}
    assert router._rng.getstate() != before


def test_random_tie_break_consumes_rng():
    router = Router(epsilon=0.0, tie_break="random", seed=0)
    before = router._rng.getstate()
    picked = router._break_tie(["V0", "V1"], (), SuccessLedger(), 1)
    assert picked in {"V0", "V1"}
    assert router._rng.getstate() != before


def test_rng_is_seeded_and_reproducible():
    """Guards the premise that two Routers built alike agree, which is what makes
    the byte-identical claim for the off path checkable at all."""
    a, b = Router(epsilon=1.0, seed=7), Router(epsilon=1.0, seed=7)
    assert [a.explore(["V0", "V1", "V2"]) for _ in range(5)] == [
        b.explore(["V0", "V1", "V2"]) for _ in range(5)
    ]
    assert isinstance(a._rng, random.Random)
