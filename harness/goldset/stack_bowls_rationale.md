# stack_bowls 金标 rationale（claude-bringup, 2026-07-29）

Run: `harness_stack_bowls_20260729_234246`。帧引用 = `frames/stageNN/tSSSS_FF.jpg`（t0007_45 ≈ 7.45s）；graph 里的 evidence_frames 按 ~26.6fps 换算核对。
总计：correct 18 / incidental 2 / wrong 4 / unsure 0 / missing 11 → P=0.818, R=0.621。

## Stage 0 — pick 左上碗（1.5–3.25s）

左臂从上方下压，指爪跨骑近侧碗沿（t0002_38），t0002_81→t0003_25 碗离桌悬空。rim grasp、桌面 clearance、lift 后 axis_vertical 全部帧支持 → correct。
`approach_direction(top_down)` 判 incidental：本 demo 自己在 stage 2 用侧向接近拿到了完全相同的 rim grasp（t0006_50–t0007_00），碗旋转对称，接近锥属等价类样本（老板原则），不变量是 rim grasp 本身。

## Stage 1 — 把左上碗放到桌面中部（3.25–5.0s）

搬运中碗基本保持水平（t0003_69、t0004_56），t0005_00 正立落桌 → axis_vertical（约束+验收）correct。
missing：`carry(gripper_holds_bowl_topleft)`——本阶段就是整段搬运，中途脱手即失败；extractor 在孪生的 stage 3 写了 carry，这里漏了。
命名缺陷：`green_bowl` 在三只全绿碗的场景里无法消歧，且与 stage 0 的 `bowl_topleft`、stage 3 的 `bowl_cl` 断链（见下"系统性问题"）。

## Stage 2 — pick 中右碗（6.0–7.0s）

rim grasp 在窗口末端成立（t0006_75 合拢、t0007_00 跨骑碗沿，裁剪图核实）→ 两条 region_grasp correct。
两条 `clearance(bowl_mr, table)` 判 wrong：整个窗口内碗从未离桌——t0006_75、边界帧 t0007_00 均有接触阴影；引证帧 175（≈6.6s）时爪子才刚到碗沿。实际离桌发生在 stage 3 窗口（t0007_45）。

## Stage 3 — 把中右碗摞到基座碗上（7.0–8.8s）

提取质量最好的阶段：carry / region_grasp / axis_vertical / center_align / inside 全部帧支持（t0007_45 搬运、t0008_35 同心入碗、t0008_80 两层柱、t0009_50 释放后不倒）→ correct。
`above` correct 但注意：引证帧 198/209（t0007_45/t0007_90）实际是贴桌高度的**侧向滑入**，above 到 ~8.3s 才成立——这也是补 `clearance(bowl_mr, bowl_cl)` 的依据（侧向进入必须不撞基座碗沿）。
`approach_direction(side, target=bowl_mr)` incidental：证据帧 175/186 是 stage 2 的抓取接近越界漏进来的，target 还写的是被搬的碗而不是放置目标；即使按抓取接近读，对称碗的 side-vs-top 之争就是 rubric 的典型 incidental。
missing 另有：`clearance(bowl_mr, table)`（离桌事件真正发生在本窗口）、`order(stage1→stage3)`（bowl_cl 就是 stage 1 放下的那只碗，依赖全图未表达）。

## Stage 4 —（graph 完全缺失）

stages.json 有 cleanup 阶段（release+retract, 8.8–9.5s），graph.json 直接没有这个 stage。帧 t0009_15–t0009_50 显示释放和缓慢后撤、两层栈保持完好。金标按 graph 现有 stage 键组织，无处挂 verdict/missing，记录于此：撤臂不扰栈（clearance(gripper, stack)）+ release 条件整段无覆盖。

## Stage 5 — pick 右上碗（10.5–13.0s）

region_grasp correct（t0012_38 合拢、t0013_00 咬合，裁剪图核实）。
两条 `clearance(green_bowl_tr, table)` 判 wrong：与 stage 2 同款错位——t0012_38、阶段末帧 t0013_00 碗都平贴桌面（接触阴影），引证帧 325（≈12.2s）同样在桌上；离桌在 stage 6 窗口（t0013_62 已悬空）。
验收列表只有这条错的 clearance，真正达成的末态（rim grasp 已锁定）反而没进验收——未计 missing（约束里已提取），记为验收侧缺陷。

## Stage 6 — 把右上碗摞上双层栈（13.0–15.5s）

灾难性漏提阶段：唯一约束 `approach_direction(top_down, bowl_stack)` 判 correct——放置侧与抓取侧不同，碗套碗只能沿栈轴向下插入（t0013_62→t0014_25 自右上下压），锥类别是承重的，自由的只是方位角；acceptance 为空。
missing×7：table clearance（离桌事件在本窗口，t0013_00→t0013_62）、carry、axis_vertical（搬运保持水平，t0014_25）、above(栈上方下放)、center_align(与栈同心，t0014_88)、inside(最终三层柱验收，t0014_88–t0015_50)、order(stage3→stage6，bowl_stack 在 stage 3 之后才存在)。
region_grasp 持持未单列 missing：与 carry 信息高度重合（rim 位置继承自 stage 5），避免 recall 分母灌水。

## 系统性问题（跨阶段）

1. **同一物体三个别名**：左上碗 = `bowl_topleft`(s0) = `green_bowl`(s1) = `bowl_cl`(s3 目标)。无共指机制 ⇒ order 依赖（s1→s3）在图上根本无法被发现；`green_bowl` 在全绿场景还额外歧义。
2. **阶段边界错位是 4 条 wrong 的共同根因**：两次 pick 阶段都按 label "grasp and lift" 声称 clearance，但视频里离桌都发生在下一阶段窗口。extractor 像是在按 stage label 想象约束，而不是看帧。
3. **order 全图零条**：三个真实先后依赖（s1→s3、s3→s6、以及隐含的 s5 在 s3 之后）一条都没提。

## 词表缺口（现实需要但词表说不出）

- **无 on/contact/support 谓词**：stage 1 的成功是"碗正立**放在桌上**且已释放"；现在只能靠 axis_vertical 代偿——一只悬空但水平的碗也能通过该验收。
- **事件式 vs 不变量式约束不分**："到阶段末达成 clearance"（事件）和"全程保持 clearance"（不变量）共用同一谓词，是边界错位型 wrong 的语义温床。
- **无 release / 抓取状态谓词**：release 条件只能塞进 holes（runtime_condition），cleanup 阶段整体不可表达（stage 4 缺失与此相关）。
- **无公差/量化槽位**：center_align、axis_vertical 均无容差参数（对碗套碗，容差≈碗口半径差，其实可从几何推出）。
- **无共指/别名机制**（schema 层缺口）：见系统性问题 1。
