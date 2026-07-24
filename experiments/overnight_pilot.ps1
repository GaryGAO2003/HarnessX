# Overnight pilot: 20 stratified tasks (8/10/2) x 6 rounds x 1 lineage,
# followed by automatic analysis.
#
# Answers four things in one run:
#   1. does the multi-round evolve/gate/revert loop survive on DeepSeek at all
#   2. how expensive is the meta-agent (the one untested cost line)
#   3. what breaks only on long runs
#   4. a first headroom signal — each round's checkpoint is a variant, which is
#      exactly what oracle_ceiling.py consumes
#
# Budget guard: the DeepSeek balance itself is the hard ceiling (~$6.5). The run
# is sized at roughly $3-4 so it cannot drain the account.

$ErrorActionPreference = "Continue"
Set-Location "D:\PycharmProj\HarnessX"

$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
$env:PYTHONUTF8 = 1
$PY = ".\.venv312\Scripts\python.exe"
$OUT = "experiments\overnight"
New-Item -ItemType Directory -Force $OUT | Out-Null

"=== PILOT START $(Get-Date -Format o) ===" | Out-File "$OUT\00-status.txt"

# ---------------------------------------------------------------- 1. the run
# --max-tasks 0 means "no cap". The GAIA recipe defaults it to 6, and the first
# pilot silently ran only the first 6 rows of a 30-row file — which, since the
# file is level-sorted, meant level 1 only. No L2/L3 means no room for variants
# to diverge, which is the whole point of the headroom measurement.
& $PY -m recipe.gaia_evolver.run `
    --model deepseek/deepseek-v4-flash `
    --meta-model deepseek/deepseek-v4-pro `
    --data-path recipe/gaia_evolver/data/pilot20.json `
    --max-tasks 0 `
    --num-rounds 6 `
    --max-steps 20 `
    --concurrency 10 `
    --max-cost 3 `
    --run-tag pilot20 `
    --clean *> "$OUT\01-run.log"

"run exit code: $LASTEXITCODE  $(Get-Date -Format o)" | Out-File "$OUT\00-status.txt" -Append

# ------------------------------------------------- 2. outcomes + real cost
& $PY experiments\analyze_run.py recipe\gaia_evolver\runs\pilot20 *> "$OUT\02-analysis.txt"
"analysis exit code: $LASTEXITCODE" | Out-File "$OUT\00-status.txt" -Append

# ------------------------------------ 3. headroom across per-round variants
& $PY recipe\gaia_evolver\oracle_ceiling.py recipe\gaia_evolver\runs\pilot20 `
    --tasks recipe\gaia_evolver\data\pilot20.json *> "$OUT\03-oracle.txt"
"oracle exit code: $LASTEXITCODE" | Out-File "$OUT\00-status.txt" -Append

# ------------------------------------------------------ 4. remaining balance
& $PY -c @"
import json, os, urllib.request
req = urllib.request.Request('https://api.deepseek.com/user/balance',
    headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY']})
d = json.load(urllib.request.urlopen(req, timeout=30))
for b in d.get('balance_infos', []):
    print(b.get('currency'), 'remaining:', b.get('total_balance'))
"@ *> "$OUT\04-balance.txt"

"=== PILOT DONE $(Get-Date -Format o) ===" | Out-File "$OUT\00-status.txt" -Append
