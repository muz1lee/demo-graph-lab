# Third-party dependencies

This file records external dependencies without vendoring their source,
weights, datasets, or runtime assets.

| Dependency | Pinned revision | License / redistribution policy | Repository policy |
|---|---|---|---|
| CoTracker | `82e02e8029753ad4ef13cf06be7f4fc5facdda4d` | CC BY-NC 4.0 | Install externally; do not vendor. Verify checkpoint SHA-256 `2670d4562ed69326dda775a26e54883925cd11b6fc9b24cb7aa9f8078bce7834`. |
| GraspNet baseline | Runtime-provided | Non-commercial and non-transferable; redistribution prohibited | Never commit its repository or weights. The repository contains only the independently written client/service wrapper. |
| graspnetAPI | Runtime-provided | MIT | Install externally when required; do not vendor for the initial import. |
| Knowin World | Deployment-provided | Internal runtime dependency | Never copy its source, scene library, task data, or assets into this repository. |

`components/robot-subtask-seg/NOTICE` must remain with that component. The
initial public repository intentionally has no open-source `LICENSE`; rights
for team-authored code remain reserved until authorship and licensing are
confirmed.

