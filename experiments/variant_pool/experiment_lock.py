# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W26 — the experiment lock: what was frozen, and what may still vary.

SPEC §6.3 (Codex critique 7): **an uninterpretable M0 baseline makes the whole
comparison worthless.** Every run writes one ``experiment.lock.json`` recording
the harness that was frozen, the models and data it ran against, and the value
of every blank the paper left us to fill (SPEC §6.6). Without it, an M0 ceiling
number cannot be attributed to anything — a later "M1 beats M0" could be a pool
effect, a different H0, a re-pulled dataset, or a silently re-tuned prior.

Three groups, two behaviours
----------------------------
The lock separates fields that define an **experiment family** from fields that
merely describe one *run inside* it:

``h0`` / ``models`` / ``dataset``
    The family. SPEC §6.3 verbatim: "H0 一经冻结,同一实验族内不得改动;改动即
    开新实验族" — once H0 is frozen it may not change inside a family; a change
    starts a new family with a new lock. :meth:`ExperimentLock.assert_same_family`
    is that rule made executable.
``hyperparams`` / ``env`` / identity
    Free to vary. A different ``K``, a different tie-break, a different seed is
    an **ablation arm**, which is the entire point of SPEC §6.6 — the blanks are
    the contribution surface, so they must be movable without voiding
    comparability.

H0 is stored as content hashes rather than content: A.4 requires H0 to be a
"competent composed harness + benchmark-specific tool registry", so what matters
is that two runs prove they used the *same* one, not that the lock carries a
copy of it. The tool registry is kept as a name list because a registry
difference (a tool added or removed) is exactly the kind of change that must
fork the family, and a bare hash would not say *which* tool moved.

Ours vs. the paper
------------------
Everything in :class:`Hyperparams` that is not marked "paper" is ours and is
listed in SPEC §6.6 with its ablation range; the paper values (``K_t = 4``,
``T = 15``, ``P = 3``, pass@2, 20 max steps, concurrency 10/4, ±5% noise
threshold, 3 seeds) come from Table 8 p.29 and A.2/A.3 p.28-29. The defaults
here mirror the module defaults in :mod:`.pool`, :mod:`.ledger`, :mod:`.router`,
:mod:`.gate` and :mod:`.target`, and a test pins them together so the lock can
never quietly drift away from the code it claims to describe.

Offline by construction
-----------------------
``created_at`` and ``git_sha`` are **injected by the caller**, not read from the
clock or from git. That keeps the module pure (SPEC §5: batch A imports nothing
from the repo and touches no network) and makes every field testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .gate import DEFAULT_MIN_FORK
from .ledger import DEFAULT_STALE_PRIOR
from .pool import DEFAULT_K

#: File name of the lock inside a run directory (SPEC §6.3).
LOCK_FILENAME = "experiment.lock.json"

#: Revision of ``SPEC.md`` this lock schema tracks. The SPEC carries no version
#: string of its own, so the date of its latest ruling section (§7, Jul-23
#: 2026) is used; bump it whenever a §6.6 default changes meaning.
SPEC_VERSION = "2026-07-23"

#: The paper's GAIA set: 103 text-only tasks, levels 39/52/12 (A.2 p.28).
PAPER_LEVEL_DISTRIBUTION = {1: 39, 2: 52, 3: 12}

#: Sections that define an experiment family (SPEC §6.3). A difference in any
#: of them is a new family; a difference anywhere else is an ablation arm.
FAMILY_SECTIONS = ("h0", "models", "dataset")


class ExperimentFamilyError(RuntimeError):
    """Raised when two locks are compared as if they were the same family.

    Not recoverable by retrying: seeing it means results from two different
    frozen harnesses, models or datasets were about to be compared as one
    experiment (SPEC §6.3).
    """


@dataclass(frozen=True)
class H0Freeze:
    """The frozen initial harness (A.4: "competent composed harness").

    Content hashes, not content — see the module docstring. ``tool_registry`` is
    the benchmark-specific registry A.4 requires, kept as an ordered name list
    so a diff can say which tool appeared or vanished.
    """

    config_sha256: str
    system_prompt_sha256: str
    tool_registry: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelSpec:
    """Inner-loop and outer-loop models, and where they were called.

    The task agent and the meta agent are separate fields because they are
    separately billed and separately swappable (SPEC §6.8 / :mod:`.accounting`),
    and because this re-implementation replaces the paper's Opus 4.6 /
    Sonnet 4.6 / GPT-5.4 with DeepSeek V4 — a substitution that must be visible
    in every lock, since it is the reason absolute scores are not comparable to
    the paper's (SPEC preamble).
    """

    task_agent_model: str
    meta_agent_model: str
    api_base: str = ""
    provider: str = "deepseek"


@dataclass(frozen=True)
class DatasetSpec:
    """The evaluation set, hashed (SPEC §6.3).

    ``level_distribution`` is carried next to the hash because it is the one
    property the sampler is *supposed* to guarantee (A.2's 39/52/12), so a lock
    that records it makes a mis-stratified subset self-evident instead of
    hiding it behind a hash nobody can invert.
    """

    path: str
    sha256: str
    size: int
    level_distribution: dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> DatasetSpec:
        """Hash and summarise a webthinker-schema task file.

        Reads the same JSON ``experiments/build_gaia_subset.py`` writes: a list
        of rows with ``task_id`` / ``Question`` / ``answer`` / ``Level``. The
        hash is over the raw bytes, so re-serialising the file with different
        whitespace counts as a different dataset — which is the conservative
        reading, since we cannot prove the content survived a rewrite.
        """
        path = Path(path)
        raw = path.read_bytes()
        rows = json.loads(raw.decode("utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"{path} must hold a JSON array of tasks")
        distribution: dict[int, int] = {}
        for row in rows:
            level = int(row["Level"])
            distribution[level] = distribution.get(level, 0) + 1
        return cls(
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            size=len(rows),
            level_distribution=dict(sorted(distribution.items())),
        )

    def matches_paper_levels(self) -> bool:
        """Is this the paper's 39/52/12 mix (A.2 p.28)?"""
        return self.level_distribution == PAPER_LEVEL_DISTRIBUTION


@dataclass(frozen=True)
class Hyperparams:
    """Every blank of the SPEC §6.6 contract table, plus the paper's Table 8.

    Each field is one row of that table: the default is the arm in force and
    the SPEC lists its ablation range. Fields marked *(paper)* are copied, not
    chosen; everything else is ours and must be reported as such.
    """

    # --- SPEC §6.6, ours -------------------------------------------------
    K: int = DEFAULT_K
    estimator: str = "laplace"
    window: int | None = None
    stale_prior: float = DEFAULT_STALE_PRIOR
    cold_start: str = "highest_rollup"
    tie_break: str = "fewest_attempts"
    epsilon: float = 0.0
    fork_inheritance: str = "transfer"
    min_fork: tuple[int, int] = DEFAULT_MIN_FORK
    retire_metric: str = "variant_rollup"
    retire_reassign: str = "reroute_orphans"
    target_strategy: str = "worst_first"
    idle_scope: str = "global"
    cluster_mode: str = "routed"
    per_variant_persistence: bool = True
    #: SPEC §7.5 — a composite ship counts towards all of its buckets.
    multi_bucket_attribution: str = "all_buckets"
    #: SPEC §7.3 — the paper's own two-timescale rule, kept verbatim.
    hit_rate_scope: str = "cumulative"
    #: SPEC §7.2 — our machine-readable Level-2 evidence type.
    level2_evidence_type: str = "level2_roundtrip"
    # --- paper ------------------------------------------------------------
    K_t: int = 4  # Evolver candidates per round (Table 8 p.29)
    T: int = 15  # rounds (Table 8)
    P: int = 3  # early-stop idle rounds (Alg. 1 L29; §6.1)
    pass_at_k: int = 2  # pass@2 (§6.1 p.15; A.3 formula 6)
    max_steps: int = 20  # GAIA step cap (Table 8)
    concurrency: int = 10  # task concurrency (Table 8)
    meta_concurrency: int = 4  # meta-agent concurrency (Table 8)
    meta_max_steps: int = 200  # meta-agent step cap (Table 8)
    noise_threshold: float = 0.05  # +-5% single-round pass-count wobble
    seeds: tuple[int, ...] = (0, 1, 2)  # 3 lineages (Table 8)

    def __post_init__(self) -> None:
        if self.K < 1:
            raise ValueError(f"K must be >= 1, got {self.K}")
        if self.K_t < 1:
            raise ValueError(f"K_t must be >= 1, got {self.K_t}")
        if self.pass_at_k < 1:
            raise ValueError(f"pass_at_k must be >= 1, got {self.pass_at_k}")
        if not 0.0 <= self.epsilon <= 1.0:
            raise ValueError(f"epsilon must be in [0, 1], got {self.epsilon}")
        if not 0.0 <= self.stale_prior <= 1.0:
            raise ValueError(f"stale_prior must be in [0, 1], got {self.stale_prior}")


@dataclass(frozen=True)
class EnvSpec:
    """Run environment: caching, RNG, and the version of the offline tooling.

    ``cache_strategy`` is load-bearing for cost, not for correctness: SPEC §6.8
    puts the whole M0 budget range ($130-310) on the cache hit rate, so a run
    that changed caching and did not say so has an uncomparable bill.

    ``seed`` is this run's RNG seed (the router's ``random`` tie-break arm);
    ``Hyperparams.seeds`` is the *family's* lineage seeds. They are separate on
    purpose: one is a run detail, the other is part of the experiment design.
    """

    cache_strategy: str = "provider_default"
    seed: int = 0
    #: git sha of ``recipe/gaia_evolver/oracle_ceiling.py`` at run time — the
    #: ceiling is what M0 is measured against (SPEC §6.3).
    oracle_ceiling_sha: str = ""


@dataclass(frozen=True)
class ExperimentLock:
    """One run's frozen configuration (``experiment.lock.json``, SPEC §6.3)."""

    experiment_id: str
    created_at: str
    git_sha: str = ""
    spec_version: str = SPEC_VERSION
    h0: H0Freeze = field(default_factory=lambda: H0Freeze("", ""))
    models: ModelSpec = field(default_factory=lambda: ModelSpec("", ""))
    dataset: DatasetSpec = field(default_factory=lambda: DatasetSpec("", "", 0))
    hyperparams: Hyperparams = field(default_factory=Hyperparams)
    env: EnvSpec = field(default_factory=EnvSpec)

    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able mapping; tuples become lists, int keys become strings."""
        return _jsonable(asdict(self))

    def to_json(self, *, indent: int | None = 2) -> str:
        """Canonical JSON. Keys are sorted so the text is order-independent."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> ExperimentLock:
        """Rebuild a lock from :meth:`to_json`, restoring tuples and int keys.

        Unknown keys are a hard error: a lock read back with a field this code
        does not understand is a lock from a different schema, and silently
        dropping it would let two incomparable runs look identical.
        """
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("an experiment lock must be a JSON object")
        return cls(
            experiment_id=data["experiment_id"],
            created_at=data["created_at"],
            git_sha=data.get("git_sha", ""),
            spec_version=data.get("spec_version", SPEC_VERSION),
            h0=_build(H0Freeze, data.get("h0", {})),
            models=_build(ModelSpec, data.get("models", {})),
            dataset=_build(DatasetSpec, data.get("dataset", {})),
            hyperparams=_build(Hyperparams, data.get("hyperparams", {})),
            env=_build(EnvSpec, data.get("env", {})),
        )

    def save(self, run_dir: str | Path) -> Path:
        """Write ``experiment.lock.json`` into a run directory."""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / LOCK_FILENAME
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> ExperimentLock:
        """Read a lock from a file, or from a run directory holding one."""
        path = Path(path)
        if path.is_dir():
            path = path / LOCK_FILENAME
        return cls.from_json(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # identity and comparison
    # ------------------------------------------------------------------

    def sha256(self) -> str:
        """Fingerprint of the whole lock, identity fields included.

        Deterministic in content, not in field order: the digest is taken over
        the sorted, separator-normalised JSON. Two locks that differ only in
        ``experiment_id`` or ``created_at`` therefore have *different*
        fingerprints — the fingerprint identifies a run, comparability between
        runs is :meth:`assert_same_family`'s job, and conflating the two is how
        a re-run silently passes for a replication.
        """
        canonical = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def diff(self, other: ExperimentLock) -> list[str]:
        """Every field that differs, as ``"dotted.path: mine -> theirs"``.

        Sorted by path so two callers reading the same pair of locks get the
        same report. Nested sections are walked recursively; a section present
        on one side only is reported as a whole.
        """
        return _diff_dicts(self.to_dict(), other.to_dict(), prefix="")

    def family_diff(self, other: ExperimentLock) -> list[str]:
        """The subset of :meth:`diff` that changes the experiment family."""
        return [entry for entry in self.diff(other) if entry.split(":", 1)[0].split(".", 1)[0] in FAMILY_SECTIONS]

    def same_family(self, other: ExperimentLock) -> bool:
        """Do both locks share H0, models and dataset (SPEC §6.3)?"""
        return not self.family_diff(other)

    def assert_same_family(self, other: ExperimentLock) -> None:
        """Raise unless both locks belong to the same experiment family.

        SPEC §6.3: H0 may not change inside a family, and changing it opens a
        new family with a new lock. Hyperparameter and environment differences
        pass — those are the ablation arms of SPEC §6.6, and refusing them
        would make the contribution surface unmeasurable.
        """
        offending = self.family_diff(other)
        if offending:
            raise ExperimentFamilyError(
                "locks belong to different experiment families (SPEC §6.3); "
                "a change here opens a new family: " + "; ".join(offending)
            )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    """Hash for prompt/config *content* (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hash for a file's raw bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    """Make ``asdict`` output JSON-safe: tuple -> list, int key -> string."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _build(cls: type, data: dict[str, Any]) -> Any:
    """Reconstruct one section, restoring the types JSON cannot carry."""
    if not is_dataclass(cls):  # pragma: no cover - defensive
        raise TypeError(f"{cls!r} is not a dataclass section")
    known = {f.name: f for f in fields(cls)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown lock field(s) {unknown}")
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        kwargs[name] = _restore(known[name].type, value)
    return cls(**kwargs)


def _restore(annotation: Any, value: Any) -> Any:
    """Undo :func:`_jsonable` for the three shapes the lock actually uses.

    Annotations arrive as strings (``from __future__ import annotations``), so
    this matches on the written type rather than on typing objects — cheap, and
    honest about only covering the shapes present in this schema.
    """
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", str(annotation))
    if isinstance(value, list) and text.startswith("tuple"):
        return tuple(value)
    if isinstance(value, dict) and text.startswith("dict[int"):
        return {int(key): item for key, item in value.items()}
    return value


def _diff_dicts(mine: dict[str, Any], theirs: dict[str, Any], *, prefix: str) -> list[str]:
    out: list[str] = []
    for key in sorted(set(mine) | set(theirs)):
        path = f"{prefix}{key}"
        left, right = mine.get(key, _MISSING), theirs.get(key, _MISSING)
        if isinstance(left, dict) and isinstance(right, dict):
            out.extend(_diff_dicts(left, right, prefix=f"{path}."))
        elif left != right:
            out.append(f"{path}: {_show(left)} -> {_show(right)}")
    return out


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<absent>"


_MISSING = _Missing()


def _show(value: Any) -> str:
    if value is _MISSING:
        return "<absent>"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
