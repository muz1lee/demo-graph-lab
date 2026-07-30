> **2026-07-30 上午更新:reach 墙已拆除。** 真因是**机器人代次错配**——C++ IK 加载 v4
> 碰撞模型(`configs/sim_cfg.runtime.yaml:3`,7/28 commit `fe9cf85` 引入)而 Genesis 跑 v3
> 机器人(场景 yaml),导致一个**与目标点无关的恒定幽灵自碰撞**(`pair_id=263`,v4 独有的
> 头部云台 `gim_p` × **左**臂 `l_joint3`,`dmin≡-0.0479`),右臂每个笛卡尔解都被否决。
> 按零污染方案修复:渲染我方 `~/phase1/cfg/sim_cfg.v3.yaml`(model + v3 `home/rest_qpos`
> 取自 v4 引入前的 commit `e33369c`)、经 `ROBOT_CONFIG`/`ROBOT_MODEL` env 重启 pipeline,
> **他们仓库零改动**。结果:幽灵碰撞 0 次;右臂 home x 0.08→**0.552**,前伸极限
> 0.24→**0.678 零拒绝**;所有目标物体(碗 0.51/0.59/0.61、管 0.436)进入包络。
> 附带结论:Remote IK 与本问题无关(`KNOWIN_IK_MODE` 在 Linux 无消费者,remote 服务包的
> 是同一个 C++ 求解器);`knowin_sim_v4verify` 是 v2 的祖先 commit,**不迁**。
> 剩余阻塞已换类:①姿态路径不可行(`rot_error` 沿路点 16°→52° 发散,`collision_free=true`)
> ②gate 空过(已修,见 `harness/gates.py`)。详见下文与 PHASE1_API_PLAN。

# Phase 1 M1a 夜间冲刺状态(2026-07-30 03:40 收工)

## 一句话

**软件链全通、物理被仿真 infra 挡住**:sim(insert_tubes/stack_bowls 场景)→EvalServer→
oracle 适配器→Opus 编译的 policy→两级 gate→episode 报告,端到端可重复运行;但两条臂在
本 checkout 里**raw IK 移动伸不进工作区**(右臂过不了 x≈0.24,物体在 x≈0.44–0.61),
躯干电机未接通,一夜零次真实抓取——与老板睡前"仿真 infra 可能不太好"的预警一致。

## 跑通了什么(全部有产物)

- 起停脚本 `scripts/phase1_sim.sh`(dgl-sim tmux;换任务=换 suite 参数;WebUI :8081)。
- 换仿真主人的完整流程(杀旧 sim → 起新 → `tmux respawn-window k1-sys:pipeline/brain`
  重 latch 传感流,已两次验证)。
- `harness/phase1.py smoke/episode` + `kwadapter.py`:oracle 求解、同义词+空间双射实体解析
  (bowl_left/mid_right/top_right 三者互异)、fire-and-forget ctrl 的 settle 等待、
  step_to 增量趋近回退、接触式 lower_until、词表几何 verify、官方谓词 probes 旁路记录。
- episode 报告:insert_tubes ×3、stack_bowls ×1(`~/phase1/artifacts/<task>/episode_*.json`,
  调用轨迹逐条留痕)。stack_bowls 显示 stage 0-2 "passed" 是**平凡真检查放行**(物体没动),
  stage 3 如实 failed——gate 语义在 oracle 模式下的漏洞已知:需要"物体位移"类硬检查,M1b 修。

## 墙:三条证据(`~/phase1/debug_grasp_evidence.json`)

1. 顶抓姿态 IK 全拒:`self_collision_violation`,pos_error 仅 1.8mm(解得到、构型自碰)。
2. 干净 reset 后右臂第一条 +x delta 即自碰,EEF 最远 x≈0.24;左臂可前伸但腕下不来(z≥1.34)。
3. 躯干救不了:`body_set_angle` 管线 ack、sim 侧 `ArmCtrl decode failed name=all_motors`
   ——body 电机组在本 sim 的 zenoh 桥里没接。
旁证:eval-runs 历史成功(stand_up_bottle/stack_bowls)全部经完整 KSM 技能栈(运动规划),
无 raw-xquat_move 成功先例;1022 上 wht 的链也是经 KSM 规划。**raw IK 直达在本环境无先例。**

## 明早的三个选项(需老板裁决)

A. **修 sim infra**(治本):body 电机桥接、或 IK 自碰撞配置(第一条前伸即自碰,疑似碰撞
   模型/边距配置问题)、或调 robot base/桌距。都在 knowin-world/k1-sys 侧,超出零污染边界,
   需要你或 infra 同学动手;我可以先出诊断 patch 建议。
B. **放行运动规划路线**(绕行):经 motion_planning 类服务下发(上游成功先例全走这条),
   但它在 `services/` 非 common 目录=当前禁区;若你按「ctrl 类可斟酌」放行,adapter 一天内接上。
C. **换可达性友好的任务/场景先出数**:扫 37 个任务族里物体更近/更高的场景
   (或 smoke 系列),先让 M1b 感知链在能动的场景上跑起来,可达性问题并行修。

## 现场状态

sim 停在 stack_bowls_000、pipeline/brain 健康、无残留进程;代码全部已提交
(HEAD 与 gitea/5090 同步);今晚 LLM 成本 $0(纯 infra,零 OpenRouter 调用)。
