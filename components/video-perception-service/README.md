# Robot Video Perception Service

Local GPU service for continuous point tracking in robot demonstration videos.
It keeps CoTracker3 resident on a dedicated GPU and exposes evidence only.

## Contract

`POST /track_points`

- Input: server-local video path and point queries `(timestamp, x, y)`.
- Output: per-frame point positions, visibility, frame timestamps, model
  provenance, and timing.
- Coordinates are returned in the caller-supplied image coordinate system.
- The service does not infer task stages, object semantics, success, 3D pose, or
  robot-frame coordinates.

## Deployment

The 1021 deployment uses:

- port `8093`, bound to `127.0.0.1`;
- the second GPU through `CUDA_VISIBLE_DEVICES=1`;
- official Meta CoTracker commit
  `82e02e8029753ad4ef13cf06be7f4fc5facdda4d`;
- `scaled_offline.pth`, SHA-256
  `2670d4562ed69326dda775a26e54883925cd11b6fc9b24cb7aa9f8078bce7834`.

CoTracker is an external research dependency. Preserve its upstream license and
attribution when redistributing the service.
