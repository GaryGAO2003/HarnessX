# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Bucket reputation + ship scoreboard (batch-4b Item 1).

A faithful port of TWO official AEGIS data modules into one recipe-side module:

* ``upstream/feat/aegis:harnessx/aegis/data/reputation.py`` — :class:`Reputation`,
  a per-bucket moving average of hit bits over a fixed window, with a boost for
  never-tried buckets to encourage exploration.
* ``upstream/feat/aegis:harnessx/aegis/data/scoreboard.py`` — :class:`ShipRecord`
  and :class:`Scoreboard`, the persistent structured ship ledger with a
  per-bucket hit-rate rollup.

The logic is ported verbatim; only the module header and this docstring differ.
The recipe persists the state flag-gated inside each round's ``pool_state.json``
(the same pattern M-51 uses for ``refuted_signatures``) rather than through the
standalone :meth:`Scoreboard.save`/:meth:`Scoreboard.load`, which are retained
for contract fidelity but unused by the wiring.

NOTE (kept from the official scoreboard docstring — a deliberate design retreat):
the scoreboard does NOT compute a coverage grid or under-exploration flags. Those
were shown to be too rigid in the v0.9 pilot; the Planner/Critic reason about
portfolio drift or under-shipped buckets in natural language from the rendered
table instead.
"""
from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Reputation (port of aegis/data/reputation.py)
# ---------------------------------------------------------------------------

BUCKETS = ("prompt", "tools", "config", "processor")
_UNKNOWN_BOOST = 0.7


class Reputation:
    """Moving average of hit_rate per 4-bucket category.

    Planner consumes this to shape brief distribution. Unknown buckets receive
    a boost to encourage exploration of unexplored spaces.
    """

    def __init__(self, window: int = 5):
        self.window = window
        self._history: dict[str, deque[bool]] = {}

    def record(self, bucket: str, hit: bool) -> None:
        dq = self._history.setdefault(bucket, deque(maxlen=self.window))
        dq.append(hit)

    def score(self, bucket: str) -> float:
        dq = self._history.get(bucket)
        if not dq:
            return _UNKNOWN_BOOST
        return sum(1 for h in dq if h) / len(dq)

    def downweight_all(self, factor: float = 0.9) -> None:
        for bucket, dq in self._history.items():
            # Drop at least 1 True if any exist, otherwise drop based on factor
            n_drop = max(1, int(len(dq) * (1 - factor)))
            for _ in range(n_drop):
                if True in dq:
                    for i, v in enumerate(dq):
                        if v:
                            dq[i] = False
                            break

    def to_dict(self) -> dict:
        return {b: list(self._history.get(b, [])) for b in BUCKETS}

    @classmethod
    def from_dict(cls, data: dict, window: int = 5) -> "Reputation":
        rep = cls(window=window)
        for bucket, history in data.items():
            rep._history[bucket] = deque(history, maxlen=window)
        return rep


# ---------------------------------------------------------------------------
# Scoreboard (port of aegis/data/scoreboard.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShipRecord:
    cid: str
    round: int
    bucket: str
    predicted_tasks: tuple[str, ...]
    flipped_in_ship_round: tuple[str, ...]

    def hit_rate(self) -> float:
        if not self.predicted_tasks:
            return 0.0
        return len(self.flipped_in_ship_round) / len(self.predicted_tasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cid": self.cid,
            "round": self.round,
            "bucket": self.bucket,
            "predicted_tasks": list(self.predicted_tasks),
            "flipped_in_ship_round": list(self.flipped_in_ship_round),
            "hit_rate": round(self.hit_rate(), 4),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ShipRecord":
        return cls(
            cid=str(d.get("cid", "")),
            round=int(d.get("round", 0)),
            bucket=str(d.get("bucket", "")),
            predicted_tasks=tuple(d.get("predicted_tasks") or ()),
            flipped_in_ship_round=tuple(d.get("flipped_in_ship_round") or ()),
        )


@dataclass
class Scoreboard:
    version: int = 1
    last_updated_round: int = 0
    ships: list[ShipRecord] = field(default_factory=list)

    def add_ship(self, rec: ShipRecord) -> None:
        # Idempotent on re-run: drop prior entry with same cid before append.
        self.ships = [s for s in self.ships if s.cid != rec.cid]
        self.ships.append(rec)
        self.ships.sort(key=lambda s: (s.round, s.cid))

    def _rollup_bucket(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for s in self.ships:
            b = s.bucket or "unknown"
            entry = out.setdefault(b, {"ships": 0, "predicted": 0, "flipped": 0})
            entry["ships"] += 1
            entry["predicted"] += len(s.predicted_tasks)
            entry["flipped"] += len(s.flipped_in_ship_round)
        for e in out.values():
            e["hit_rate"] = (
                round(e["flipped"] / e["predicted"], 4) if e["predicted"] else 0.0
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "last_updated_round": self.last_updated_round,
            "ships": [s.to_dict() for s in self.ships],
            "by_bucket": self._rollup_bucket(),
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "Scoreboard":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls()
        sb = cls(
            version=int(data.get("version", 1)),
            last_updated_round=int(data.get("last_updated_round", 0)),
        )
        for entry in data.get("ships") or []:
            try:
                sb.ships.append(ShipRecord.from_dict(entry))
            except Exception:
                continue
        sb.ships.sort(key=lambda s: (s.round, s.cid))
        return sb
