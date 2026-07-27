#!/usr/bin/env bash
# Local inference stack on 1022 (wenqian-owned ports only).
# Ports:
#   16068  SAM3-compatible shim (Grounded-SAM-2 / SAM2.1)
#   15079  GraspGen /infer worker   (WIP)
#   15093  GraspPipeline_Re         (WIP — blocked on pinocchio/IK rebuild)
set -euo pipefail
STACK=${LOCAL_STACK_ROOT:-/mnt/data/wenqian/local_stack}
REPO=${REPO_ROOT:-/mnt/data/wenqian/demo-graph-lab}
mkdir -p "$STACK/logs"

start_sam3() {
  if curl -sf "http://127.0.0.1:16068/health" >/dev/null; then
    echo "[sam3_shim] already up on :16068"
    curl -s "http://127.0.0.1:16068/health"; echo
    return 0
  fi
  echo "[sam3_shim] starting on :16068 (GPU ${CUDA_VISIBLE_DEVICES:-0})"
  CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0} \
  GROUNDED_SAM2_ROOT=$STACK/Grounded-SAM-2 \
  PYTHONPATH=$STACK/Grounded-SAM-2 \
  SAM3_SHIM_PORT=16068 SAM3_SHIM_DEVICE=cuda:0 \
  nohup $STACK/sam2_run_venv/bin/python $STACK/sam3_shim/server.py \
    >$STACK/logs/sam3_shim.log 2>&1 &
  echo $! >$STACK/logs/sam3_shim.pid
  for i in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:16068/health" >/dev/null; then
      curl -s "http://127.0.0.1:16068/health"; echo
      return 0
    fi
    sleep 2
  done
  echo "[sam3_shim] FAILED to become healthy; see $STACK/logs/sam3_shim.log" >&2
  return 1
}

case "${1:-all}" in
  sam3|all) start_sam3 ;;
  *) echo "usage: $0 [sam3|all]"; exit 2 ;;
esac
