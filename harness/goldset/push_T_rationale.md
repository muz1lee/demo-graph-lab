# push_T 金标 rationale（claude-bringup, 2026-07-29）

Run: `harness/runs/harness_push_T_20260729_235655/`
判定对象：`graph.json`（2 core stages，10 个 verdict 条目）。帧号按 ~25 fps 映射到秒，与关键帧文件名一一对应：
f000=t0000_00, f025=t0001_00, f050=t0002_00, f075=t0003_00, f100=t0004_00, f128=t0005_12, f156=t0006_25, f184=t0007_38, f212=t0008_50, f235=t0009_40（stage02）。

## 场景事实（帧证据基线）

- 目标 "pad" 是桌面上**灰色 T 形填充标记**，不是无朝向的方形区域（f000 放大可辨，bar+stem 轮廓完整）。任务备注里的疑问就此落地：**pad 显式指定朝向**，axis 类目标约束按 core 判，不降级 incidental。
- 全程**无抓取**：右臂夹爪闭合成尖端，指尖降到桌面高度以**侧面接触**推/拨（f025、f050、f075、f184 均清晰）。graph 里也零 `region_grasp`/`carry`——预警的幻觉模式未出现。
- **取向测量**（方法：红/灰颜色分割 → 最大连通域 → PCA 主轴 + 沿次轴三阶矩定 stem 指向；末态自检通过——f235 块取向与 f000 pad 取向差 +2.4°）。块相对 pad 的朝向差轨迹：
  - f000 **−11°**（初始几乎已对齐！）→ f050 +10° → f075 **+76°**（无遮挡，可信）→ f100 **~+120°**（红块约 20% 被夹爪遮挡，估计有噪，但 z 放大图确认大角度错开）→ f156 +76°（重遮挡，仅 102px，参考值）→ f184 **+3.8°** → f212 **+2.0°** → f235 **+2.4°**。
  - 灰 pad 可见像素：f000 基线 524 → f235 **~39**（撤臂无遮挡）＝末态**完整覆盖**。
- 也就是说本 run 的真实故事是：**初始朝向本来就近似对齐，粗推的偏心侧推把它越推越歪（−11°→~+120°），stage1 再用约 4.5 s 把 ~120° 的旋转误差收回到 ~2°**。这与 push_T_random 卷（stage0 完成大部分旋转、边界残差 20–30°）事实相反，直接导致两卷 stage-0 axis_parallel 的判定相反——同一规则、不同 demo 事实。

## Stage 0 `push`（0–4.0 s）：3 constraints + 2 acceptance → 3 correct / 2 wrong；missing 0

- `center_align{t_block.center, pad.center}` — **correct**。本阶段驱动目标：f000 块在 pad 右侧约 100px，f075 已推到重叠，f100 大体压住 pad。违反＝块没到 pad，任务必败。粗容差由 `align_tolerance` hole 承接。
- `approach_direction{cone: side, target: t_block}` — **correct**。f025 指尖降至桌面高度从块右侧水平接近；f050/f075 尖端顶住柄端侧面推进，块全程贴桌。非抓取推动里 side/top_down 仰角类别有力学意义（顶压产生不了平面位移），属 rubric 说的"demo 教的接触关系"（从上/从侧仰角类别），不是 symmetry-free 方位——T 也没有对称性可豁免。故 correct 而非 incidental。
- `axis_parallel{t_block.stem_axis, pad.stem_axis}` — **wrong**（本卷最重的一刀）。该约束在本阶段不但没被维持，而是**被 demo 自己的推动动作系统性打破**：−11°(f000) → +76°(f075) → ~+120°(f100)。其自引证据帧 0/75/100 恰好横跨这段"越推越歪"。把它放进 stage0 的执行语义是"边推边保持柄轴平行"，与偏心侧推的物理相反；真正的对齐发生在 stage1（f184→f212）。约束对任务**终态**为真，但阶段归属错误。
- acceptance `center_align`（f100）— **correct**。f100 块已压住 pad，粗对位达成，残余由 stage1 收尾；容差语义由 hole 表达。
- acceptance `axis_parallel`（f100）— **wrong**。被其唯一引用的 f100 直接反驳：边界处朝向差 ~+120°（f075 无遮挡已 +76°），任何合理容差下都不成立；按此验收 demo 自己都过不了 stage0。这是"把终态目标前移进粗推验收"的典型错误。
- missing：无。侧面接触已由 approach_direction 覆盖；"推哪个面"词表表达不了（见词表缺口 1）；"块保持平贴桌面"低位薄块推动风险极小、demo 无风险帧，不进 missing（同 push_T_random 卷口径）。

## Stage 1 `fine_alignment`（4.0–8.5 s）：3 constraints + 2 acceptance → 4 correct / 1 wrong；missing 2

- `axis_parallel{t_block.long_axis, pad.long_axis}` — **correct**。pad 是 T 形标记、朝向被显式指定，T 无旋转对称 → 核心。测量：~+120°(f100) → +3.8°(f184，灰边仅剩细条) → +2.0°(f212，完全盖住)。注意：本 run 的"fine"阶段实际承担了 ~120° 的大角度旋转整形，名不副实但约束本身成立。stage0 叫 `stem_axis`、stage1 叫 `long_axis`——同义异名，键匹配时会被当成不同约束（词表缺口 4）。
- `center_align{t_block.center, pad.center}` — **correct**。f212 完全覆盖；f235 撤臂后灰残留 ~39px（基线 524）定量确认。
- `approach_direction{cone: top_down, target: t_block}` — **wrong**。五个引证帧（f100/f128/f156/f184/f212）里接触均为**桌面高度的侧向轻推**：f184 最清晰——闭合指尖贴在柄的侧面；f128/f156 有遮挡但指尖可见位于块侧旁桌面高度；从未见指尖压在块顶面发力。块贴桌完成 ~120°→2° 的平移+旋转，力学上必须侧向力。头顶的腕部/前臂姿态易被视觉误读成 top_down——这是"EEF 到达方向"与"接触仰角"的混淆（3/5 票、conf 0.51 也反映 ensemble 摇摆）。判 wrong 而非 unsure：f100/f184 无歧义，遮挡帧不影响结论。
- acceptance `axis_parallel` / `center_align`（f212）— 均 **correct**：完全覆盖，+2.0°；f235 无遮挡终态确认。
- **missing 1：`approach_direction{cone: side, target: t_block}`**。执行本阶段所必需的侧面接触约束在 graph 里不存在（存在的是错值 top_down）。判定口径见 judgment call 2。与 push_T_random 金标 stage1 的同名 missing 对齐（该卷是漏提，本卷是错提，但"图里没有可执行的正确约束"这一点相同）。
- **missing 2：`inside{obj: t_block, region: pad}`**。`center_align + 无向 axis_parallel` 联合欠定平面位姿：绕形心转 180° 的 T 同时满足两者，但横杠换端、盖不住标记 → 任务失败而验收通过。demo 末态（f235 完整覆盖）证明目标是 footprint containment。闭词表内 `inside` 是唯一能钉死翻转 DoF 的条目；与 push_T_random 金标 missing 2 同一判法（跨卷一致性）。备选拼法 `center_align{t_block.bar_center, pad.bar_center}` 也能破翻转，但需要发明子部件特征名，弃用。

## Judgment calls（记录供 PI 复核）

1. **stage-0 acceptance `axis_parallel` 判 wrong，与 push_T_random 卷同名条目判 correct 相反**。非标注口径漂移，是 demo 事实相反：该卷 stage0 边界残差 20–30°（粗容差成立），本卷 ~+120° 且趋势是发散的（f075→f100 还在变大）。两卷对照恰好构成"同一约束、按帧证据分道"的一致性样本。
2. **错值条目的双记账**：stage1 的 `approach_direction` 错值（top_down）计 wrong 进 precision 分母，同时正确值（side）计 missing 进 recall 分母。理由：从执行者视角，graph 给出的约束集里不存在可用的侧面接触约束，recall 应反映这一点。若 PI 认为一次错误只应记一账（只留 wrong），删 missing 1 即可，P 不变、stage-1 recall 升。
3. **stage-0 constraint `axis_parallel` 是 wrong 不是 incidental**：incidental 要求"demo 里确实如此"。本阶段 demo 里它不成立（且越来越不成立），不满足 incidental 的前提。
4. **top_down 判 wrong 不判 unsure**：f128/f156 确有遮挡，但 f100/f184 两帧接触几何无歧义，且力学论证（贴桌平移+旋转需要侧向力）独立于遮挡帧成立。
5. **测量佐证的地位**：像素级取向测量只是佐证，判定仍以帧内可见接触/覆盖关系为准；f100 的 ~+120° 因遮挡有噪，结论依赖的是无遮挡的 f075(+76°) 加放大图目视确认。

## 词表缺口（只记录，不进 gold）

1. **接触侧 / 推动方向无法表达**（最尖锐）：`approach_direction` 的 cone 只有仰角类别，说不出"在物体坐标系里接触哪个侧面"（应推背离 pad 的面）。对非对称块这是推动任务第一承重约束。目前全靠 holes 兜底——本卷 `push_contact_point`（两阶段都 5/5 票，solver_hint 明确写 "contact face opposite to push direction"）、`push_direction`(4/5)。词表需要 contact-side/push-direction 原语。
2. **平面完整位姿对齐无原语**：`center_align + 无向 axis_parallel` 留 180° 翻转 DoF（见 stage1 missing 2）。需要 `pose_align`/footprint-match，或至少把 axis_parallel 定义成有向轴（区分 parallel/antiparallel）。
3. **过程约束 vs 终态约束不分**：stage0 的 axis_parallel 两条 wrong 本质是"终态目标被写成了粗推阶段的过程/验收约束"。schema 若区分 goal-of-task 与 goal-of-stage，这类错误可被结构性避免。
4. **特征命名漂移**：同一物理轴在 stage0 叫 `stem_axis`、stage1 叫 `long_axis`，holes 同步漂移（`align_tolerance` vs `alignment_tolerance`）。按 key 精确匹配的 metrics 会把同义约束当不同键，跨阶段聚合时会低估一致性。
5. **cleanup 阶段（stage 2, 8.5–9.4 s）无抽取条目**：撤臂若在桌面高度横扫会毁掉已对齐结果。demo 里指尖先抬升再撤（f212→f235，末态未被破坏，灰残留 ~39px 不变）。graph.json 没有 stage 2 槽位，gold 无处挂 `clearance(gripper, t_block)`——harness 结构缺口，与 push_T_random 卷同款，不计入本卷 missing。

## 正面观察

- 零抓取污染：无 `region_grasp`/`carry` 幻觉。
- evidence_frames 与实际事件吻合度高：stage1 `axis_parallel` 只引 f184/f212（恰是旋转收敛后的两帧），stage0 `approach_direction` 引 f025–f075（恰是接近+建立接触段）。
- holes 质量高且"推动感知"正确（trailing-face 接触点、块心→pad 心投影方向、容差标量），信息在，只是约束词表装不下。

## 过程备注

- 标注基于 stage00/stage01/stage02 各 5 张关键帧 + 关键区域 4× 放大裁剪；取向测量脚本一次性运行于 scratchpad（方法见"场景事实"），未写入 run 目录。
- graph.json 单一版本：mtime 23:59（与 validation.json/report.html/cost.jsonl 同为管线末次写盘），本标注开始前已定稿、期间未变化，无 push_T_random 卷那样的中途重写问题。
