# Grasp Proposal HTTP Service Wrapper

This is a standalone service wrapper for a GraspNet baseline deployment. It is
not wired into the Codex Skill Harness runtime.

## Modes

- `backend=graspnet_baseline`: load GraspNet baseline code and checkpoint.
- `backend=fixture`: return configured sample proposals for transport/schema
  smoke tests only.

Fixture mode is never evidence that a real grasp detector works. `/health`
reports the active backend and readiness state.

## Minimal Start

在 **1022** `demo-graph-lab` 内启动；配置路径放在被忽略的本地文件中，不要写进 1024
`/mnt/nas/knowin_sim/sim_workspace/`。旧 `/mnt/workspace/wht/...` 路径已作废。

```bash
export GRASPNET_SERVICE_CONFIG=/path/to/local/config.json
python -m uvicorn app:app --host 127.0.0.1 --port 8091
```

## Local runtime notes (1022)

Standalone sandbox pointers (host-local; not the 1024 base tree):

- Fixture smoke service: `http://127.0.0.1:8091`
- GraspNet baseline service: `http://127.0.0.1:8092`
- Baseline repo / checkpoint: provide via local config (do not vendor weights here)

The baseline service needs the PyTorch/CUDA library path when started:

```bash
export GRASPNET_SERVICE_CONFIG=/path/to/local/config.graspnet_baseline.json
export LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/torch/lib:/usr/local/cuda-12.1/lib64:${LD_LIBRARY_PATH}
python -m uvicorn app:app --host 127.0.0.1 --port 8092
```

The original Google Drive checkpoint can be quota-limited. The tested
`checkpoint-rs` file was downloaded from a Hugging Face mirror and verified with
`torch.load` to contain `epoch`, `loss`, `model_state_dict`, and
`optimizer_state_dict`.

## Endpoints

- `GET /health`
- `POST /predict`

The service passes detector coordinates through unchanged. It does not transform
camera coordinates into robot/world coordinates and does not select the final
grasp for a task.
