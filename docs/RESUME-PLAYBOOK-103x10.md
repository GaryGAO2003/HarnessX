# 103x10 双臂复活手册（2026-08-13 夜）

任一臂死亡（进程消失/早停/重启）后的续跑命令。原则：`--start-round N`，
N = curves.json 里最后一个已记分轮 + 1。新进程 noop_streak 归零；播种自
R(N-1)/applied/merged.yaml（无则 R(N-1)/config.yaml），曲线历史自动恢复。

## L2（主 checkout，D:\PycharmProj\HarnessX）

```bash
cd /d/PycharmProj/HarnessX && PYTHONUTF8=1 ./.venv312/Scripts/python.exe \
  -m recipe.gaia_evolver.run_meta_aegis_ghx --ghx-level 2 \
  --tasks recipe/gaia_evolver/data/webthinker_gaia_dev.json \
  --num-rounds 10 --max-tasks 0 \
  --model deepseek-v4-flash --meta-model deepseek-v4-pro \
  --search-backend serper --run-tag L2_103x10 \
  --start-round <N> >> recipe/gaia_evolver/runs_L2_103x10.console.log 2>&1 &
```

## L0（baseline worktree 为 cwd，主 checkout 的 venv）

```bash
cd /d/PycharmProj/HarnessX-baseline && PYTHONUTF8=1 \
  /d/PycharmProj/HarnessX/.venv312/Scripts/python.exe \
  -m recipe.gaia_evolver.run_meta_aegis \
  --tasks recipe/gaia_evolver/data/webthinker_gaia_dev.json \
  --num-rounds 10 --max-tasks 0 \
  --model deepseek-v4-flash --meta-model deepseek-v4-pro \
  --search-backend serper --run-tag L0_103x10 \
  --start-round <N> >> recipe/gaia_evolver/runs/L0_103x10.out.log \
  2>> recipe/gaia_evolver/runs/L0_103x10.err.log &
```

## 查 N

```bash
python -c "import json;print(1+max(r['round'] for r in json.load(open('<run_dir>/curves.json',encoding='utf-8'))))"
```

## 注意

- 模型 id 必须小写 `deepseek-v4-flash`（大写别名路由到坏部署，504）。
- `--search-backend serper` 两臂必须一致。
- 半跑的 R<N> 目录无需清理——任务重跑覆盖；孤儿 meta 半成品在分析期按
  "无消费者"排除（见 build log 2026-08-12 · L2 首次续跑条目）。
- 判废标准：单轮 run_loop error 洪水（哨兵阈值 ≥10/5min）→ 该轮弃用重跑。
- 授权记录：用户 2026-08-13 00:4x"保持他跑完，非重大事故自动续跑"。
  重大事故（端点持续性死亡/数据目录损坏/成本失控）→ 停手 + 推送通知。
