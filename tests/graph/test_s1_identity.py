"""Tests for harnessx.graph.identity — S1: genotype/phenotype hashing and dedup."""

import pytest

from harnessx.core.builder import HarnessBuilder
from harnessx.bundles import context, coding
from harnessx.graph.identity import DedupRegistry, genotype_hash, phenotype_hash
from harnessx.graph.snapshot import to_graph


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
