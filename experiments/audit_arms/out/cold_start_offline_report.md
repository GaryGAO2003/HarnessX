# Sim B -- Cold-Start Router Shrinkage (offline LFO replay)

- runs used: s1k8b103
- runs skipped: [('a1big4', 'variants<8'), ('a1big5', 'variants<8'), ('a1pilot2', 'variants<8'), ('a1pilot3', 'variants<8'), ('a1pilot', 'variants<8'), ('a1smoke', 'variants<8'), ('aegis1', 'variants<8'), ('calib6', 'variants<8'), ('calib6b', 'variants<8'), ('calib_p12_a', 'variants<8'), ('calib_p12_b', 'variants<8'), ('e_pervar3', 'variants<8'), ('fixsmoke1', 'variants<8'), ('forceprobe1', 'variants<8'), ('forceprobe2', 'variants<8'), ('forkprobe', 'variants<8'), ('forkprobe_11', 'variants<8'), ('forkprobe_p2', 'variants<8'), ('labsmoke1', 'variants<8'), ('organic1', 'variants<8'), ('paper1', 'variants<8'), ('paper2', 'variants<8'), ('paper3', 'variants<8'), ('paper4', 'variants<8'), ('pilot20', 'variants<8'), ('pilot30', 'variants<8'), ('resumedrill1', 'variants<8'), ('s1k8', 'variants<8'), ('smoke_calib6', 'variants<8'), ('smoke_hard2', 'variants<8'), ('smoke_hard3', 'variants<8'), ('smoke_hard', 'variants<8'), ('smoke_sub', 'variants<8'), ('smoke_vp3', 'variants<8'), ('smoke_vp', 'variants<8')]
- constants: m_grid=[1, 2, 4, 8] unknown_boost=0.7 drop_infra=True min_variants=8

## cluster-key = task

- prediction points: 1438 (empty-history cells: 373); n=1 flip points: 13
- best shrinkage by Brier: shrink@1

| estimator | Brier | log-loss | n scored | n=1 flips |
|---|---|---|---|---|
| mle@0.5 | 0.15659 | 0.80733 | 2836 | 8 |
| mle@0.7 | 0.17391 | 0.84465 | 2836 | 8 |
| shrink@1 | 0.16832 | 0.50148 | 2836 | 8 |
| shrink@2 | 0.17006 | 0.5061 | 2836 | 8 |
| shrink@4 | 0.17504 | 0.52033 | 2836 | 6 |
| shrink@8 | 0.18317 | 0.54195 | 2836 | 6 |

## cluster-key = level

- prediction points: 1438 (empty-history cells: 154); n=1 flip points: 0
- best shrinkage by Brier: shrink@1

| estimator | Brier | log-loss | n scored | n=1 flips |
|---|---|---|---|---|
| mle@0.5 | 0.21938 | 0.6294 | 2836 | 0 |
| mle@0.7 | 0.22612 | 0.64395 | 2836 | 0 |
| shrink@1 | 0.22116 | 0.63442 | 2836 | 0 |
| shrink@2 | 0.2213 | 0.63437 | 2836 | 0 |
| shrink@4 | 0.22163 | 0.63474 | 2836 | 0 |
| shrink@8 | 0.22205 | 0.63533 | 2836 | 0 |

> Audit reading: raw MLE emits 0/1 point predictions after one rollout, so a single observation crosses the 0.5 routing threshold (n=1 flips); shrinkage holds the estimate near its prior and cuts those flips. Log-loss (calibration) is where shrinkage gains most; the official 0.7 unknown-boost inflates error on empty-history cells vs a neutral 0.5 on this low-pass-rate benchmark.
