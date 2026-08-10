"""Tests for harnessx.graph.identity — S1: genotype/phenotype hashing and dedup."""

import pytest

from harnessx.core.builder import HarnessBuilder
from harnessx.bundles import context, coding
from harnessx.graph.identity import DedupRegistry, genotype_hash, phenotype_hash
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import GraphSnapshot, Node, NodeType


class TestGenotypeHash:
    def test_returns_hex_string(self):
        config = (HarnessBuilder() | context).build()
        snapshot = to_graph(config)
        gh = genotype_hash(snapshot)
        assert isinstance(gh, str)
        assert len(gh) == 64  # SHA-256
        assert all(c in "0123456789abcdef" for c in gh)

    def test_same_config_same_hash(self):
        c1 = (HarnessBuilder() | context).build()
        c2 = (HarnessBuilder() | context).build()
        gh1 = genotype_hash(to_graph(c1))
        gh2 = genotype_hash(to_graph(c2))
        assert gh1 == gh2

    def test_different_configs_different_hash(self):
        c1 = (HarnessBuilder() | context).build()
        c2 = (HarnessBuilder() | context | coding).build()
        gh1 = genotype_hash(to_graph(c1))
        gh2 = genotype_hash(to_graph(c2))
        assert gh1 != gh2

    def test_hash_is_deterministic(self):
        config = (HarnessBuilder() | context | coding).build()
        snapshot = to_graph(config)
        hashes = [genotype_hash(snapshot) for _ in range(10)]
        assert len(set(hashes)) == 1

    def test_hash_excludes_observed_edges(self):
        """Genotype hash should not include observed edges."""
        config = (HarnessBuilder() | context).build()
        snapshot = to_graph(config)
        gh = genotype_hash(snapshot)
        # Should still compute (observed edges might be empty)
        assert len(gh) == 64


class TestPhenotypeHash:
    def test_returns_hex_string_or_empty(self):
        config = (HarnessBuilder() | context).build()
        snapshot = to_graph(config)
        ph = phenotype_hash(snapshot)
        assert isinstance(ph, str)
        assert len(ph) == 64

    def test_same_config_same_phenotype(self):
        c1 = (HarnessBuilder() | context).build()
        c2 = (HarnessBuilder() | context).build()
        ph1 = phenotype_hash(to_graph(c1))
        ph2 = phenotype_hash(to_graph(c2))
        assert ph1 == ph2


class TestPathCtorKwargHashing:
    """Path-typed constructor kwargs hash via ``as_posix`` (regraph_failed fix).

    A replayed config whose processor ``__init__`` turns string kwargs
    (template_path / db_path / base_dir) into ``pathlib.Path`` objects used to
    crash the hash kernel with ``TypeError: Object of type WindowsPath is not
    JSON serializable``.  These lock in the ``as_posix`` normalization and the
    strictness guard.
    """

    @staticmethod
    def _proc_snapshot(ctor_kwargs: dict) -> GraphSnapshot:
        node = Node(
            node_id="p1",
            node_type=NodeType.PROCESSOR,
            label="MyProc",
            metadata={"_target_": "pkg.MyProc", "_ctor_kwargs_": ctor_kwargs},
        )
        return GraphSnapshot(nodes={"p1": node}, edges=[])

    def test_windows_and_posix_path_kwargs_hash_deterministically(self):
        """A node carrying WindowsPath / PosixPath ctor kwargs hashes without
        crashing and is deterministic across repeated calls."""
        from pathlib import PurePosixPath, PureWindowsPath

        snap = self._proc_snapshot({
            "template_path": PureWindowsPath("data", "template.db"),
            "db_path": PurePosixPath("var", "app.db"),
        })
        hashes = [genotype_hash(snap) for _ in range(5)]
        assert len(hashes[0]) == 64
        assert len(set(hashes)) == 1  # deterministic, no TypeError

    def test_native_path_kwarg_does_not_crash(self):
        from pathlib import Path

        snap = self._proc_snapshot({"base_dir": Path("work") / "run-1"})
        assert len(genotype_hash(snap)) == 64

    def test_path_and_forward_slash_string_hash_equal(self):
        """as_posix canonicalization is intentional: ``Path('a/b')`` and the
        string ``'a/b'`` are value-equivalent and MUST hash identically."""
        from pathlib import Path

        path_snap = self._proc_snapshot({"p": Path("a/b")})
        str_snap = self._proc_snapshot({"p": "a/b"})
        assert genotype_hash(path_snap) == genotype_hash(str_snap)

    def test_windows_backslash_path_normalizes_to_posix(self):
        """Backslashes must NOT leak into the hash — otherwise the same config
        would hash differently on Windows vs POSIX."""
        from pathlib import PureWindowsPath

        win_snap = self._proc_snapshot({"p": PureWindowsPath("a\\b\\c")})
        str_snap = self._proc_snapshot({"p": "a/b/c"})
        assert genotype_hash(win_snap) == genotype_hash(str_snap)

    def test_unknown_object_still_raises_type_error(self):
        """Strictness guard: only ``PurePath`` is normalized; any other unknown
        type keeps raising ``TypeError`` so the hash never silently absorbs it."""
        class Weird:
            pass

        snap = self._proc_snapshot({"x": Weird()})
        with pytest.raises(TypeError):
            genotype_hash(snap)


class TestDedupRegistry:
    def test_new_hash_registers(self):
        reg = DedupRegistry()
        assert reg.register("abc123", "C-01")
        assert len(reg) == 1

    def test_duplicate_rejected(self):
        reg = DedupRegistry()
        reg.register("abc123", "C-01")
        assert not reg.register("abc123", "C-02")
        assert len(reg) == 1

    def test_is_duplicate(self):
        reg = DedupRegistry()
        reg.register("abc123", "C-01")
        assert reg.is_duplicate("abc123")
        assert not reg.is_duplicate("xyz789")

    def test_first_seen(self):
        reg = DedupRegistry()
        reg.register("abc123", "C-01")
        assert reg.first_seen("abc123") == "C-01"
        assert reg.first_seen("xyz789") is None

    def test_contains(self):
        reg = DedupRegistry()
        reg.register("abc123", "C-01")
        assert "abc123" in reg
        assert "xyz789" not in reg
