# Test fixtures — provenance

## `real_session_R9_V0_0383a3ee.jsonl`

Verbatim copy (byte-identical, 33075 bytes, 30 records) of a real landed
session JSONL produced by `harnessx/tracing/journal.py`.

- **Source path** (on the machine that produced it):
  `recipe/gaia_evolver/runs/e_pervar3/R9/active_pool/V0/sessions/R9-V0-active-0383a3ee-47a7-41a4-b493-519bdefe0488/1dd39555-e4e2-4b3a-908a-c1e7d737a797.jsonl`
- **Copied**: 2026-08-05
- **Task**: GAIA `0383a3ee-47a7-41a4-b493-519bdefe0488` — "On the BBC Earth
  YouTube video of the Top 5 Silliest Animal Moments, what species of bird is
  featured?"
- **Companion trajectory md** (source of the expected `final_output` /
  `extracted_answer` hard-coded in `test_event_replay_adapter.py`):
  `recipe/gaia_evolver/runs/e_pervar3/R9/active_pool/V0/trajectories/0383a3ee-47a7-41a4-b493-519bdefe0488.md`
  (`extracted_answer: "rockhopper penguin"`; `## Result > final_output` is the
  full final assistant message ending in `FINAL ANSWER: rockhopper penguin`).

Anti-recurrence: the official gate died of "fixture schema != producer schema".
This fixture is the **producer's own output**, unedited, so the adapter is
tested against exactly what the journal writes — not a hand-authored mock.
Regenerate by re-copying the source path above (schema:
`session_start`/`tools`/`system`/`raw_user`/`raw_assistant`/`raw_tool`/`episode_end`).
