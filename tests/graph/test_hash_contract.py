"""Executable pins for the graph hash contract (governs every later module).

The genotype hash is computed over ``snapshot.nodes`` / ``snapshot.edges`` only,
and it is sensitive to *any* ordinary metadata key on a persistent node.  That
constraint is invisible until it silently changes a digest, so it is pinned here
as loud, executable tests rather than left to be rediscovered.

Caching note: ``identity.genotype_hash`` (and siblings) recompute on every call
and only *then* cache onto the snapshot field.  Every assertion below compares
the *return value* of a fresh call — never the cached ``snap.genotype_hash``
field — so a stale cache cannot produce a false pass.
"""

from harnessx.graph.identity import (
    deployment_hash,
    genotype_hash,
    phenotype_hash,
)
from harnessx.graph.types import (
    EDGE_FAMILY,
    Edge,
    EdgeFamily,
    EdgeType,
    GraphSnapshot,
    Node,
    NodeType,
    edge_family,
)


def _sample_snapshot() -> GraphSnapshot:
    """A small hand-built graph spanning several node/edge types + metadata.

    Deliberately a literal (not ``to_graph``) so the pinned digest below is a
    function of the IR contract alone, not of the exporter's current behaviour.
    """
    nodes = {
        "hook:before_tool": Node(
            node_id="hook:before_tool",
            node_type=NodeType.SKELETON_HOOK,
            label="before_tool",
            metadata={"hook_name": "before_tool"},
        ),
        "hook:task_end": Node(
            node_id="hook:task_end",
            node_type=NodeType.SKELETON_HOOK,
            label="task_end",
            metadata={"hook_name": "task_end"},
        ),
        "hook:step_start": Node(
            node_id="hook:step_start",
            node_type=NodeType.SKELETON_HOOK,
            label="step_start",
            metadata={"hook_name": "step_start"},
        ),
        "proc:sample": Node(
            node_id="proc:sample",
            node_type=NodeType.PROCESSOR,
            label="Sample",
            metadata={
                "_target_": "mod.Sample",
                "_hook_": "before_tool",
                "_order_": 50,
                "_writes_slots_": ["memory"],
            },
        ),
        "slot:memory": Node(
            node_id="slot:memory",
            node_type=NodeType.SLOT,
            label="memory",
            metadata={"slot_name": "memory", "slot_type": "SharedMemory"},
        ),
        "bundle:demo": Node(
            node_id="bundle:demo",
            node_type=NodeType.BUNDLE,
            label="demo",
            metadata={"child_graph_id": "g1"},
        ),
    }
    edges = [
        Edge(
            source_id="proc:sample",
            target_id="hook:before_tool",
            edge_type=EdgeType.ATTACHED_TO,
            metadata={},
        ),
        Edge(
            source_id="proc:sample",
            target_id="slot:memory",
            edge_type=EdgeType.WRITES_TO,
            metadata={"provenance": "declared", "data_channel": "state"},
        ),
        Edge(
            source_id="hook:task_end",
            target_id="hook:step_start",
            edge_type=EdgeType.LOOP_BACK,
            metadata={},
        ),
        Edge(
            source_id="bundle:demo",
            target_id="proc:sample",
            edge_type=EdgeType.COMPOSES_WITH,
            metadata={},
        ),
    ]
    return GraphSnapshot(nodes=nodes, edges=edges)


# Computed by running the code (scripts/pin_probe), NOT invented.  A change to
# this literal means the genotype-hash CONTRACT moved — a deliberate decision
# that invalidates every previously stored genotype — never a test to be
# casually re-baselined.  If this fails, find out WHY the canonical form changed
# before touching the string.
_PINNED_GENOTYPE = "58badba16c62a82ccac05958feed33046b0976f869833f3382eb48d0cfa73452"


# ── 1. pin ──────────────────────────────────────────────────────────────────


def test_genotype_hash_pinned():
    assert genotype_hash(_sample_snapshot()) == _PINNED_GENOTYPE


# ── 2. sensitivity: any ordinary metadata key moves the genotype ────────────


def test_ordinary_metadata_key_changes_genotype():
    snap = _sample_snapshot()
    before = genotype_hash(snap)
    # Node is a frozen dataclass, but its metadata dict is mutable (hash=False).
    snap.nodes["proc:sample"].metadata["arbitrary_key"] = "anything"
    assert genotype_hash(snap) != before


# ── 3. the one exclusion: _code_hash is carved out (identity.py:139) ────────


def test_code_hash_excluded_from_genotype():
    snap = _sample_snapshot()
    before = genotype_hash(snap)
    # Adding _code_hash must not move the genotype.
    snap.nodes["proc:sample"].metadata["_code_hash"] = "deadbeef"
    assert genotype_hash(snap) == before
    # Changing an existing _code_hash must also not move it.
    snap.nodes["proc:sample"].metadata["_code_hash"] = "cafef00d"
    assert genotype_hash(snap) == before


# ── 4. isolation: runtime overlay is invisible to genotype, visible to deploy ─


def test_runtime_overlay_isolated_from_genotype_but_moves_deployment():
    snap = _sample_snapshot()
    g_before = genotype_hash(snap)
    d_before = deployment_hash(snap)

    snap.runtime_nodes["rt:extra"] = Node(
        node_id="rt:extra",
        node_type=NodeType.PROCESSOR,
        label="Extra",
        metadata={"_target_": "mod.Extra", "_runtime_only": True},
    )
    snap.runtime_edges.append(
        Edge(
            source_id="rt:extra",
            target_id="hook:before_tool",
            edge_type=EdgeType.ATTACHED_TO,
            metadata={"provenance": "runtime_only"},
        )
    )

    assert genotype_hash(snap) == g_before  # I6: genotype ignores the overlay
    assert deployment_hash(snap) != d_before  # deployment sees it


# ── 5. observed edges: move phenotype, not deployment ───────────────────────


def test_observed_edges_move_phenotype_not_deployment():
    snap = _sample_snapshot()
    d_before = deployment_hash(snap)
    p_before = phenotype_hash(snap)

    snap.edges.append(
        Edge(
            source_id="proc:sample",
            target_id="hook:before_tool",
            edge_type=EdgeType.OBSERVED_CONTROL,
            metadata={"observation_count": 3},
        )
    )

    assert deployment_hash(snap) == d_before  # observed excluded from deployment
    assert phenotype_hash(snap) != p_before  # phenotype includes observed


# ── EDGE_FAMILY: explicit total coverage (no silent STRUCTURAL default) ──────


def test_invokes_maps_to_control_flow():
    assert EDGE_FAMILY[EdgeType.INVOKES] == EdgeFamily.CONTROL_FLOW
    assert edge_family(EdgeType.INVOKES) == EdgeFamily.CONTROL_FLOW


def test_edge_family_covers_every_edge_type():
    # edge_family() silently defaults unknown types to STRUCTURAL; require every
    # member to have an explicit entry so a new edge type fails loudly here
    # instead of being mis-classified as structural.
    missing = [e for e in EdgeType if e not in EDGE_FAMILY]
    assert not missing, f"EdgeType members missing an explicit EDGE_FAMILY entry: {missing}"
