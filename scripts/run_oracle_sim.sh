#!/usr/bin/env bash
# 启动 Oracle 调试所需的仿真服务。
# 用法: bash scripts/run_oracle_sim.sh tasks/robodojo/insert_tubes/insert_tubes_000.suite.yaml
# 启动后可运行：dgl-oracle smoke --task-id robodojo_insert_tubes_000
set -euo pipefail
SUITE="${1:?need suite yaml relative to KNOWIN_DATA_ROOT}"
KNOWIN_SIM_ROOT="${KNOWIN_SIM_ROOT:?set KNOWIN_SIM_ROOT to the simulator checkout}"
DGL_ORACLE_RUNS_ROOT="${DGL_ORACLE_RUNS_ROOT:-$HOME/demo-graph-lab-runs/oracle}"
DGL_ORACLE_LOG="${DGL_ORACLE_LOG:-$DGL_ORACLE_RUNS_ROOT/sim.log}"
source "$KNOWIN_SIM_ROOT/bootstrap.env"
mkdir -p "$DGL_ORACLE_RUNS_ROOT"
SESSION="dgl-sim"
tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" \
  "cd \"$KNOWIN_REPO\" && .venv/bin/python -m sim.runtime \
     --data-root \"$KNOWIN_DATA_ROOT\" --tasks \"$SUITE\" \
     --serve --serve-port 7480 --backend cuda \
     --web-host 0.0.0.0 --web-port 8081 \
     --pipeline-url http://127.0.0.1:8000 \
     --k1-skill-root \"$KNOWIN_K1_SYS_ROOT\" \
     --runs-root \"$DGL_ORACLE_RUNS_ROOT\" \
     --no-record-state-trajectory 2>&1 | tee -a \"$DGL_ORACLE_LOG\""
echo "started tmux session $SESSION; log: $DGL_ORACLE_LOG"
