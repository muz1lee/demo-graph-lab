# 第三方依赖说明

本文件只记录外部依赖，不 vendor 其源码、权重、数据集或运行时资产。

| 依赖 | 钉扎修订 | 许可 / 再分发 | 本仓策略 |
|---|---|---|---|
| CoTracker | `82e02e8029753ad4ef13cf06be7f4fc5facdda4d` | CC BY-NC 4.0 | 外部安装；勿 vendor。校验 checkpoint SHA-256 `2670d4562ed69326dda775a26e54883925cd11b6fc9b24cb7aa9f8078bce7834`。 |
| GraspNet baseline | 运行时提供 | 非商业且不可转让；禁止再分发 | 永不提交其仓库或权重；本仓只有独立编写的客户端/服务包装。 |
| graspnetAPI | 运行时提供 | MIT | 需要时外部安装；首轮导入不 vendor。 |
| Knowin World | 部署侧提供 | 内部运行时依赖 | 永不复制源码、场景库、任务数据或资产进本仓。技能迭代在 1022 `demo-graph-lab` 完成，不改 1024 基础仓。 |

`components/robot-subtask-seg/NOTICE` 必须随组件保留。公开仓首轮故意不放开源 `LICENSE`；团队代码权利保留，待归属与授权确认后再定。
