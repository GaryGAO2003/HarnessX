# Sim A -- Racing Acceptance Gate (offline replay)

- runs used: s1k8b103
- runs skipped: [('a1big4', 'variants<8'), ('a1big5', 'variants<8'), ('a1pilot2', 'variants<8'), ('a1pilot3', 'variants<8'), ('a1pilot', 'variants<8'), ('a1smoke', 'variants<8'), ('aegis1', 'variants<8'), ('calib6', 'variants<8'), ('calib6b', 'variants<8'), ('calib_p12_a', 'variants<8'), ('calib_p12_b', 'variants<8'), ('e_pervar3', 'variants<8'), ('fixsmoke1', 'variants<8'), ('forceprobe1', 'variants<8'), ('forceprobe2', 'variants<8'), ('forkprobe', 'variants<8'), ('forkprobe_11', 'variants<8'), ('forkprobe_p2', 'variants<8'), ('labsmoke1', 'variants<8'), ('organic1', 'variants<8'), ('paper1', 'variants<8'), ('paper2', 'variants<8'), ('paper3', 'variants<8'), ('paper4', 'variants<8'), ('pilot20', 'variants<8'), ('pilot30', 'variants<8'), ('resumedrill1', 'variants<8'), ('s1k8', 'variants<8'), ('smoke_calib6', 'variants<8'), ('smoke_hard2', 'variants<8'), ('smoke_hard3', 'variants<8'), ('smoke_hard', 'variants<8'), ('smoke_sub', 'variants<8'), ('smoke_vp3', 'variants<8'), ('smoke_vp', 'variants<8')]
- variant pairs: 56 | true-effect pairs streamed: 2492 | overall discordant rate: 0.2705
- constants: alpha=0.05 beta=0.2 p1_grid=[0.55, 0.6, 0.75] B=2000 seed=20260805 drop_infra=True

## 1. True-effect SPRT decisions

| p1 | implied uplift | ACCEPT | REJECT | CONTINUE | n_used median/mean |
|---|---|---|---|---|---|
| 0.55 | 2.705pp | 0 (0.0) | 0 (0.0) | 56 (1.0) | 60.0/44.5 |
| 0.6 | 5.409pp | 0 (0.0) | 12 (0.2143) | 44 (0.7857) | 34.5/41.57 |
| 0.75 | 13.523pp | 15 (0.2679) | 28 (0.5) | 13 (0.2321) | 22.0/26.59 |

### ACCEPT count by observed-uplift bin
| p1 | <=-5pp | (-5,-1]pp | (-1,1]pp | (1,3]pp | (3,5]pp | (5,10]pp | >10pp |
|---|---|---|---|---|---|---|---|
| 0.55 | A0/R0/C21 | A0/R0/C3 | A0/R0/C8 | A0/R0/C3 | A0/R0/C0 | A0/R0/C5 | A0/R0/C16 |
| 0.6 | A0/R12/C9 | A0/R0/C3 | A0/R0/C8 | A0/R0/C3 | A0/R0/C0 | A0/R0/C5 | A0/R0/C16 |
| 0.75 | A0/R20/C1 | A0/R3/C0 | A1/R1/C6 | A0/R3/C0 | A0/R0/C0 | A1/R1/C3 | A13/R0/C3 |

## 2. A/A calibration (true null, cluster bootstrap)

- A/A pairs: 1492 across 103 (run,task) clusters, B=2000
| p1 | false-ACCEPT rate | 95% CI | within alpha? |
|---|---|---|---|
| 0.55 | 0.034 +/- 0.0041 | [0.0261, 0.0419] | True |
| 0.6 | 0.047 +/- 0.0047 | [0.0377, 0.0563] | True |
| 0.75 | 0.0865 +/- 0.0063 | [0.0742, 0.0988] | False |

## 3. Budget reallocation (racing vs fixed-N)

| p1 | fixed-N pairs | racing pairs | saved | frac saved | saved(early REJECT) |
|---|---|---|---|---|---|
| 0.55 | 2492 | 2492 | 0 | 0.0 | 0 |
| 0.6 | 2492 | 2328 | 164 | 0.0658 | 164 |
| 0.75 | 2492 | 1489 | 1003 | 0.4025 | 600 |

> Honesty check (audit): at 1-3pp observed uplift the gate almost never ACCEPTs -- racing manufactures no power, it only reallocates budget.
