# Phase 1 执行绑定:Method-visible API v1 与适配器方案(2026-07-30)

依据:①老板底座规则(可用=arm_node 级控制 + `knowin_reasoner/services/common`;禁人手 skill yaml
与非 common reasoner;感知对照 cap-x/GaP 复现;ctrl 新增先斟酌;零污染原仓);②5090 底层勘察
(remote-namespace 扩展机制、9 个 common 服务、VisualProcessor 取图链);③capgym/cap-x 与
graph-robots/graph-as-policy 两仓 API 挖掘(CaP-X 从 182 个 LLM 生成函数蒸馏出 73 个技能——
「LLM 反复重造的轮子清单」是感知集优先级的经验证据)。

## 1. 架构(零污染)

```text
compiled policy.py(不透明句柄,不变)
   │ rt.*
kwadapter.py(本仓,可信 runtime)
   ├─ ctrl   → pipeline :8000 /run action=ctrl(现有 14 个 USABLE 原语,零新增)
   ├─ solve  → ①common 服务(经 /run action=reasoning:qwen/sam_xquat[:dof]、pixels_base3d、existence)
   │           ②dgl-perception(本仓外挂 HTTP 服务,CaP-X 微服务风格:8114/8115 同构)
   ├─ verify → M1a/b: EvalServer GET /state 谓词 probe(官方同源;标注 privileged-eval)
   │           M2+:  几何自证(自家感知) + query_yes_no(非 oracle 路径)
   └─ oracle solve(仅 M1a) → GET /state 实体位姿(标注 ORACLE,只作集成测试与上界)
仿真:自起 python -m sim.runtime --serve :7480(任务套件参数化);共用现有 zenoh/pipeline,不起重复服务
```

- k1-sys/knowin-world **零文件改动**;dgl-perception 全部住本仓 `harness/perception_service/`。
- 不经 pipeline 注册 remote-namespace(那需要重启共享 pipeline):适配器直连自家感知服务;
  remote-ns 登记方式留档,将来需要 skill-yaml 级寻址时再用(反正我们不用 yaml)。

## 2. 感知 API v1(12 个,承重排序;两仓交集 + CaP-X 蒸馏频次)

| # | API(签名简) | v1 后端 | 服务的洞 |
|---|---|---|---|
| 1 | `get_observation() → {rgb,depth,K,T_world_cam}` | VisualProcessor(直连 sim capture_bridge,外部进程自建) | 一切之根 |
| 2 | `segment_text(rgb,query) → [{mask,box,score}]` | dgl-perception(SAM3/Grounded-SAM2,权重视 5090 现状) | point/pose 上游 |
| 3 | `detect(rgb,query) → [{box,label,score}]` | common `gdino_xquat` 的检测层或 dgl 复现 | 同上 |
| 4 | `mask_to_world_points(mask,depth,K,T) → (N,3)` | 纯 numpy(本仓) | point_3d |
| 5 | `filter_noise(points) → points` | DBSCAN(两仓参数一致) | 质量 |
| 6 | `compute_obb(points) → {center,extent,R}`(**约定:全边长**) | open3d(本仓) | pose/axis/scalar |
| 7 | `get_object_pose(name) → (pos,quat_wxyz,extent)` | 优先 common `qwen/sam_xquat:dof`;dgl 复合兜底 | pose_se3 |
| 8 | `sample_grasps(depth,K,segmap) → (poses相机系,scores)` | **GraspNet 从 1022 移植**,按 CaP-X 服务协议(/plan,/plan_point_clouds;相机系原始位姿,TCP 偏移留调用方) | grasp pose |
| 9 | `select_top_down_grasp(...)` + `top_down_grasp_from_obb(...)` | 纯几何(本仓;OBB 推导作无 GraspNet 退化路) | grasp pose |
| 10 | `point_prompt(rgb,text) → (u,v)` | M2(Molmo 类;先缺省) | 兜底 |
| 11 | `transform_points / pixel_to_world_point` | 纯 numpy(本仓) | point/axis |
| 12 | `query_yes_no(rgb,question) → bool` | M2(VLM;非 oracle 验证的主来源) | runtime_condition |

四元数统一 **wxyz**;OBB extent 统一**全边长**(cap-x 全长 vs GaP 半长的坑,文档写死)。
API 实现即文档(抄 CaP-X `combined_doc`:inspect.signature+docstring 自动生成 prompt 面)。

## 3. ctrl 映射(零新增,全部现有原语)

approach/transport/align→`xquat_move`(z_arc 插值);grasp_at→`xquat_move`+`set_gripper`;
lift→`delta_move`(+z);lower_until→`delta_move`(−z,小步+state 轮询);push→`follow_delta_trajectory`;
release→`set_gripper`。参数面严格按 `../docs/reference/PRIMITIVE_API.md` 的 USABLE 列。
抄 GaP 的 tag+限额:感知/控制调用各设预算,防调用爆炸。

## 4. 防火墙细则(比两仓都严的差异点)

GaP 把 `sim.check_success` 直接暴露给图、CaP-X 有 privileged API 且不与视觉判据类型隔离——
我们相反:oracle(/state 实体态、谓词 probe)只进 evaluator/上界,类型上与方法可见 API 隔离;
方法路径的 runtime_condition 只能来自 #12/几何自证。这条写进论文就是与两家的对照差异。

## 5. Bring-up 阶梯

- **M1a 集成冒烟(oracle 标注)**:起 `sim.runtime` insert_tubes_000 → kwadapter(oracle solve +
  pipeline ctrl)跑编译好的 policy 全链 → gate 用 /state probes。产出:适配器/控制链通,oracle 上界。
- **M1b 方法路径 v1**:dgl-perception 上线(#1-#9,GraspNet 移植),solve 切非特权;同任务重跑,
  报五阶段 funnel。前置:探测 common 服务的模型权重现状(qwen/sam 是否可用,风险见 §6)。
- **M1c 首批数字**:insert_tubes + stack_bowls 各 20 seeds(场景 000-164 现成),冻结 policy,
  首份成功率+funnel 报告。
- push_T 挂起(老板指示);deposit_coin 随后。

## 6. 风险

1. common 服务的视觉模型权重在 5090 的可用性未验(sam/qwen/gdino;有 `KNOWIN_ALLOW_UNAVAILABLE_SERVICES`
   逃生门)——M1b 前置探测,缺则 dgl-perception 自带(Grounded-SAM-2 shim 有 D2' 前科可抄)。
2. IK 可达性:K1 前伸 ~0.63m;oracle 位姿可能超域,`xquat_move` 在本 checkout 未冒烟过。
3. 共享服务互踩:pipeline :8000 当前绑着哪个 sim 进程未定;M1a 起我们的 sim 后要验证 ctrl 真的
   落到我们的场景(get_xquat 回读 + WebUI 目视)。
