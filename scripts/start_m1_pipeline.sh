#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/configs/env.sh"
export PATH="$ROOT/tools/bin:/mnt/nas/knowin_sim/sim_workspace/bin:${PATH:-}"
K1_SYS_DIR="$KNOWIN_REPO/sim/sys/k1-sys-v0"
PYTHON_BIN="${UV_PROJECT_ENVIRONMENT}/bin/python"
PIPELINE_LOG="$KNOWIN_SIM_ROOT/logs/pipeline.log"
RUNTIME_SECRETS_PREFIX="$(
  KNOWIN_SIM_ROOT="$KNOWIN_SIM_ROOT" \
  bash "$KNOWIN_REPO/scripts/runtime_secrets.sh" --print-source-prefix
)"
# Refresh runtime env file
{
  echo "export DOF_PICK_GRASPGEN_TIMEOUT_S=8.0"
  echo "export DOF_PICK_DOF_TIME_BUDGET_S=10.0"
  echo "export DOF_PIPELINE_SERVER_URL=http://101.132.143.105:5093"
  echo "export GRASP_PICKPLACE_DOF_PIPELINE_SERVER_URL=http://101.132.143.105:5093"
  bash "$K1_SYS_DIR/run_sim.sh" --print-skill-env-prefix
} > "$ROOT/configs/pipeline_runtime.env"

tmux kill-session -t knowin_pipeline 2>/dev/null || true
tmux kill-window -t k1-sys:pipeline 2>/dev/null || true
pkill -f "python.*pipeline_node.py" 2>/dev/null || true
sleep 1

tmux new-session -d -s knowin_pipeline "\
cd \"$K1_SYS_DIR\" && \
set -a && source env/k1-sys.env && set +a && \
$RUNTIME_SECRETS_PREFIX \
set -a && source \"$ROOT/configs/env.sh\" && source \"$ROOT/configs/pipeline_runtime.env\" && set +a && \
export PATH=\"$ROOT/tools/bin:/mnt/nas/knowin_sim/sim_workspace/bin:\$PATH\" && \
export ROBOT_MODEL=\"$ROBOT_MODEL\" && \
export ROBOT_CONFIG=\"$ROBOT_CONFIG\" && \
export PYTHONPATH=\"$KNOWIN_REPO/sim:$KNOWIN_REPO/sim/sys/k1-sys-v0:$KNOWIN_REPO\" && \
\"$PYTHON_BIN\" pipeline_node.py \"$ROBOT_MODEL\" > \"$PIPELINE_LOG\" 2>&1"
echo "started knowin_pipeline"
