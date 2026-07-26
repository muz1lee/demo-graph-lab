# knowin_world 适配器

## 正式模式（EvalServer）

```text
POST /session/reset → POST /skill → POST /session/finalize
```

- `/skill` 响应必须带精确 `queue_id` 且 `quiescence_confirmed is True`
- 丢弃响应里的 `state`（含仿真 GT）
- `finalize` 产物是 `OracleFinalRecord`，只给隔离评测，不进 Method Broker

## 开发模式

- WebUI `5049`：取 frame / debug
- pipeline `8000`：`PipelineClient` 调 `/run`

## Runtime Doctor

```bash
python -m adapters.knowin_world.runtime_doctor --config configs/examples/runtime.example.json
```

dirty 依赖可记录 diff hash，但只能标 non-golden；正式 benchmark 必须 clean pinned。
