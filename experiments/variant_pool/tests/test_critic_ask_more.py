# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--critic-ask-more N`` (batch-4b Item 2).

The orchestrated ask-more loop, faithful to
``upstream/feat/aegis:harnessx/aegis/stages/judge.py`` but adapted to OUR bare
``_LLMCritic`` completion: the Critic MAY return an ``ask_evolver`` JSON block;
each question is answered by one tool-less "mini-evolver" provider completion
over the candidate's manifest + config, appended to the candidate's
``critic_qa.md`` and to the re-judgment prompt, and the Critic re-invoked (capped
at N; past the cap a verdict is forced). N=0 is byte-identical: no contract in
the prompt, exactly one completion.

Fully offline: the meta provider is a scripted FIFO fake — never a network call.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.critic import CriticContext, DeterministicCritic  # noqa: E402
from experiments.variant_pool.evidence import EvidenceStore  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402


class _ScriptedProvider:
    """A FIFO queue of response strings; records every prompt seen.

    Serves BOTH the Critic completions and the tool-less answer completions
    (both go through ``self.provider.complete``), so the queue interleaves them.
    """

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):
        self.calls += 1
        self.prompts.append(messages[0].content)
        if not self.responses:
            raise AssertionError("no scripted response left")
        return SimpleNamespace(content=self.responses.pop(0))


def _artifact(root: Path, candidate_id: str = "C-R3-01") -> CandidateArtifact:
    output_dir = root / "artifacts" / candidate_id
    output_dir.mkdir(parents=True, exist_ok=True)
    config = output_dir / "config.yaml"
    config.write_text(f"candidate: {candidate_id}\nprocessors: []\n", encoding="utf-8")
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": ["prompt"],
            "file_changes": [
                {"path": "p.txt", "action": "modify", "diff_summary": "x"}
            ],
            "predicted_impact": {"tasks_will_unlock": ["t-fail"]},
            "target_variant": "V0",
        }
    )
    return CandidateArtifact(config_path=config, manifest=manifest, target_variant="V0")


def _context() -> CriticContext:
    # No regressions ⇒ the deterministic whole-round veto never fires.
    return CriticContext(
        round_idx=3, target_variant="V0", digests=(), regressions=(), failure_buckets=()
    )


def _critic(root: Path, provider, *, ask_more: int = 0) -> rvp._LLMCritic:
    return rvp._LLMCritic(
        provider=provider,
        fallback=DeterministicCritic(EvidenceStore(root / "evstore")),
        ask_more_rounds=ask_more,
    )


def _ask(cid: str, question: str) -> str:
    return json.dumps({"ask_evolver": [{"candidate_id": cid, "question": question}]})


def _verdict(cid: str = "C-R3-01", *, extra: dict | None = None) -> str:
    obj = {
        "ranked_candidate_ids": [cid],
        "verdicts": [{"candidate_id": cid, "rank": 1, "reasons": ["ok"]}],
        "rejections": [],
        "revision_requests": [],
        "no_op": False,
        "no_op_reasons": [],
    }
    if extra:
        obj.update(extra)
    return json.dumps(obj)


def _run(critic, candidates):
    return asyncio.run(critic.review(context=_context(), candidates=candidates))


# ---------------------------------------------------------------------------
# 1. question → answer → re-judge: 3 completions, verdict returned, Q&A on disk
#    and in the re-judgment prompt.
# ---------------------------------------------------------------------------
def test_ask_more_question_answer_rejudge_flow(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [_ask("C-R3-01", "why prompt-only?"), "because it is enough", _verdict()]
    )
    critic = _critic(tmp_path, provider, ask_more=2)
    cand = _artifact(tmp_path)
    review = _run(critic, (cand,))

    assert provider.calls == 3  # critic-ask, mini-evolver answer, critic-verdict
    assert "C-R3-01" in review.ranked_candidate_ids
    # The ask-more contract was present in the FIRST critic prompt.
    assert "Ask-more protocol" in provider.prompts[0]
    # The mini-evolver answer prompt carried the candidate's question + config.
    assert "why prompt-only?" in provider.prompts[1]
    assert "candidate: C-R3-01" in provider.prompts[1]
    # The re-judgment (3rd) prompt carried the accumulated Q&A + the answer.
    assert "Evolver answers to your questions so far" in provider.prompts[2]
    assert "because it is enough" in provider.prompts[2]
    # The Q&A was appended to the candidate-scoped critic_qa.md.
    qa = (tmp_path / "artifacts" / "C-R3-01" / "critic_qa.md").read_text(encoding="utf-8")
    assert "## Critic Q&A (ask-more round 1)" in qa
    assert "why prompt-only?" in qa and "because it is enough" in qa


# ---------------------------------------------------------------------------
# 2. cap enforcement: with N=1 a second ask_evolver (alongside a verdict) is
#    NOT honored — the verdict is forced and only round 1's Q&A is written.
# ---------------------------------------------------------------------------
def test_ask_more_cap_forces_verdict(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            _ask("C-R3-01", "first question"),
            "first answer",
            # past the cap: carries ask_evolver ALONGSIDE a valid verdict.
            _verdict(extra={"ask_evolver": [{"candidate_id": "C-R3-01", "question": "second question"}]}),
        ]
    )
    critic = _critic(tmp_path, provider, ask_more=1)
    cand = _artifact(tmp_path)
    review = _run(critic, (cand,))

    # Exactly one ask-more turn (2 critic calls + 1 answer); the 2nd question is
    # never answered.
    assert provider.calls == 3
    assert "C-R3-01" in review.ranked_candidate_ids
    qa = (tmp_path / "artifacts" / "C-R3-01" / "critic_qa.md").read_text(encoding="utf-8")
    assert "ask-more round 1" in qa
    assert "first question" in qa
    assert "ask-more round 2" not in qa
    assert "second question" not in qa


# ---------------------------------------------------------------------------
# 3. N=0 (default): no contract in the prompt, exactly one completion — the
#    byte-identical pin.
# ---------------------------------------------------------------------------
def test_ask_more_zero_is_byte_identical(tmp_path: Path) -> None:
    provider = _ScriptedProvider([_verdict()])
    critic = _critic(tmp_path, provider, ask_more=0)
    cand = _artifact(tmp_path)
    review = _run(critic, (cand,))

    assert provider.calls == 1
    assert "C-R3-01" in review.ranked_candidate_ids
    assert "Ask-more protocol" not in provider.prompts[0]
    # No candidate_qa.md is created when ask-more is off.
    assert not (tmp_path / "artifacts" / "C-R3-01" / "critic_qa.md").exists()


# ---------------------------------------------------------------------------
# 4. An ask_evolver block for an unknown candidate is answered honestly ("no
#    such candidate") and still re-judged, rather than crashing the review.
# ---------------------------------------------------------------------------
def test_ask_more_unknown_candidate_is_handled(tmp_path: Path) -> None:
    # The unknown candidate short-circuits _answer_question WITHOUT a provider
    # call, so no answer response is consumed: only the two critic completions
    # run (ask, then re-judge).
    provider = _ScriptedProvider([_ask("C-NOPE", "who?"), _verdict()])
    critic = _critic(tmp_path, provider, ask_more=1)
    cand = _artifact(tmp_path)
    review = _run(critic, (cand,))
    assert "C-R3-01" in review.ranked_candidate_ids
    assert provider.calls == 2
    # The honest "no such candidate" answer still reaches the re-judgment prompt.
    assert "no such candidate" in provider.prompts[1]
