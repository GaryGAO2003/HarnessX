"""Is the NEW-vs-BASE gap noise? Three layers.

L1 seed noise      : 1000 seeds each, Welch t, per-seed win rate, bootstrap CI
L2 compute parity  : NEW consumes more gate measurements (it has viable
                     candidates). Cap NEW at K=2 / K=1 slots and compare its
                     gate-measurement budget AND outcome against BASE at K=4.
L3 model assumptions: one-at-a-time knob flips; report the gap's sign.
"""
import importlib
import random
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sim_decision as S


def runs(pol, seeds, **kw):
    return [S.run_policy(pol, s, **kw) for s in range(seeds)]


def summ(rs):
    dep = [x["deployed"] for x in rs]
    gm = statistics.mean(x.get("gate_meas", 0) for x in rs)
    return statistics.mean(dep), statistics.pstdev(dep), dep, gm


print("=" * 96)
print("L1  种子噪声(1000 种子)")
print("=" * 96)
mb, sb, db, gmb = summ(runs("BASE", 1000))
mn, sn, dn, gmn = summ(runs("NEW", 1000))
gap = mn - mb
se = ((sb * sb + sn * sn) / 1000) ** 0.5
win = sum(1 for a, b in zip(dn, db) if a > b) / 1000
rng = random.Random(0)
boots = []
for _ in range(2000):
    boots.append(statistics.mean(rng.choices(dn, k=1000)) - statistics.mean(rng.choices(db, k=1000)))
boots.sort()
lo, hi = boots[50], boots[1949]
ceiling = (sum(p if S.KIND[t] != "flaky" else 0.95 for t, p in S.P0.items())) / len(S.TASKS)
print(f"BASE {mb:.4f} ±{sb:.4f}   NEW {mn:.4f} ±{sn:.4f}")
print(f"差 = {gap*100:+.2f}pp   Welch SE={se*100:.3f}pp   t≈{gap/se:.1f}")
print(f"逐种子胜率 P(NEW>BASE) = {win*100:.1f}%   bootstrap 95% CI = [{lo*100:+.2f}, {hi*100:+.2f}]pp")
print(f"天花板(摇摆全修至 .95)≈ {ceiling:.4f};余地利用率 BASE {100*(mb-0.7758)/(ceiling-0.7758):.0f}% "
      f"vs NEW {100*(mn-0.7758)/(ceiling-0.7758):.0f}%")

print("\n" + "=" * 96)
print("L2  算力对等(门测量条数 = 候选评测成本的代理)")
print("=" * 96)
print(f"{'臂':<18}{'K槽':>5}{'部署真值':>10}{'门测条数/跑':>13}{'相对BASE':>10}")
print(f"{'BASE':<18}{4:>5}{mb:>10.4f}{gmb:>13.0f}{'1.00×':>10}")
for k in (4, 2, 1):
    S.K_SLOTS = k
    m2, s2, d2, g2 = summ(runs("NEW", 500))
    w2 = sum(1 for a, b in zip(d2, db[:500]) if a > b) / 500
    print(f"{'NEW':<18}{k:>5}{m2:>10.4f}{g2:>13.0f}{g2/gmb:>9.2f}×   胜率 {w2*100:.0f}%")
S.K_SLOTS = 4

print("\n" + "=" * 96)
print("L3  模型假设敏感性(单旋钮翻转,300 种子,报差值符号)")
print("=" * 96)
KNOBS = [
    ("主设定", {}),
    ("dud 率 0.3→0.5", {"P_DUD": 0.5}),
    ("修复增益 .35→.20", {"FIX_GAIN": 0.20}),
    ("误伤率 .25→.40", {"P_COLLATERAL": 0.40}),
    ("摇摆可修率 .6→.4", {"P_FIX_FLAKY": 0.40}),
    ("每候选目标 3→2", {"TARGETS_PER_CAND": 2}),
    ("死题可修 .05→.6", {}),
]
print(f"{'设定':<22}{'BASE':>9}{'NEW':>9}{'差':>9}{'胜率':>7}")
for label, over in KNOBS:
    saved = {k: getattr(S, k) for k in over}
    for k, v in over.items():
        setattr(S, k, v)
    pfd = 0.6 if "死题" in label else 0.05
    b = runs("BASE", 300, p_fix_dead=pfd)
    n = runs("NEW", 300, p_fix_dead=pfd)
    bm = statistics.mean(x["deployed"] for x in b)
    nm = statistics.mean(x["deployed"] for x in n)
    w = sum(1 for a, c in zip((x["deployed"] for x in n), (x["deployed"] for x in b)) if a > c) / 300
    print(f"{label:<22}{bm:>9.4f}{nm:>9.4f}{(nm-bm)*100:>+8.2f}p{w*100:>6.0f}%")
    for k, v in saved.items():
        setattr(S, k, v)
