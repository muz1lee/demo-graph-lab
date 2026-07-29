#!/usr/bin/env bash
# 起 Phase-1 评测仿真(照抄 knowin_sim_v2/bin/consolidate_full_v2.sh 的启动形态,换任务参数)。
# 用法: bash scripts/phase1_sim.sh tasks/robodojo/insert_tubes/insert_tubes_000.suite.yaml
# 只起我们自己的 sim.runtime(:7480/:8080);共用现有 zenoh/pipeline,不起重复服务。
set -euo pipefail
SUITE="${1:?need suite yaml relative to KNOWIN_DATA_ROOT}"
V2="$HOME/knowin_sim_v2"
source "$V2/bootstrap.env"
SESSION="dgl-sim"
tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" \
  "cd $KNOWIN_REPO && .venv/bin/python -m sim.runtime \
     --data-root $KNOWIN_DATA_ROOT --tasks $SUITE \
     --serve --serve-port 7480 --backend cuda \
     --web-host 0.0.0.0 --web-port 8080 \
     --pipeline-url http://127.0.0.1:8000 \
     --k1-skill-root $KNOWIN_K1_SYS_ROOT \
     --runs-root $HOME/phase1/eval-runs \
     --no-record-state-trajectory 2>&1 | tee -a $HOME/phase1/sim.log"
echo "started tmux session $SESSION; log: ~/phase1/sim.log"
